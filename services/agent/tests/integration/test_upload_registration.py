"""An upload answers within register_response_s whatever its document backend does (timeout budget
PR, scope C, I5; ADR-0254).

The finding (docs/BACKLOG.md, High): an MCP registration could take up to 62 s (a full semaphore,
then the call) against the gateway's 60 s, so the user saw a failed upload while the file was
registered behind it. Now the hand-over runs as work of its own: past register_response_s (20 s,
config/docs.yaml) the upload answers `pending` and the registration goes on in the background,
within the server's semaphore and timeout_s; its outcome is kept on the record, and a failure ends
the file `failed` with `register_failed`, final, its chunks never returned by search.

The reference RAG server (tests/fixtures/rag_ref.py: its own role and database) runs in a thread
with an event loop of its own, so its indexing never shows in the lag the test measures, with its
`register` wrapped: held 25 s, or indexing the file and then failing the call. Timings are printed
for docs/PROGRESS.md (rule 11)."""

import asyncio
import threading
import time
import uuid

import pytest
import uvicorn

from app.attachments.service import TooManyUploads
from app.guardrails.engine import GuardEngine
from app.mcp.client import MCPClient
from app.mcp.config import load_mcp_config, resolve
from app.rag.backend import DocumentScope
from app.rag.mcp import MCPDocumentBackend
from app.tools.broker import ToolBroker
from app.tools.registry import ToolRegistry
from app.tools.spec import ToolScope
from tests.fixtures.doc_ops_mcp import DocumentOpsMCPServer, connected
from tests.fixtures.documents import pdf_bytes
from tests.fixtures.fake_backend import with_backend
from tests.fixtures.mcp_server import free_port
from tests.fixtures.rag_ref import TOKEN, rag_ref_database_url
from tests.fixtures.timing import LagProbe

pytestmark = pytest.mark.integration

RESPONSE_BOUND_S = 20.0  # config/docs.yaml register_response_s
REGISTER_HELD_S = 25.0
LAG_BOUND_S = 0.1
PAGE = ["لائحة الإجازات", "مدة إجازة الأمومة سبعون يوما بأجر كامل"]


@pytest.fixture
async def rag(settings, litellm):
    """The reference RAG server with its `register` under the test's control (`mode`), and the
    session's services on MCPDocumentBackend over it."""
    from mcp_ref.rag.server import build_app, build_pipeline

    pipeline = build_pipeline(settings, rag_ref_database_url())
    mode: dict[str, object] = {"held_s": 0.0, "fail_after_index": False, "entered": threading.Event()}
    register = pipeline.documents.register

    async def controlled(fields):
        mode["entered"].set()  # the server's own thread: a threading.Event
        await asyncio.sleep(float(mode["held_s"]))
        await register(fields)
        if mode["fail_after_index"]:
            # The server keeps the file and indexes it, then fails the call: the platform must
            # never return what it indexed.
            await pipeline.queue.put(fields["id"])
            raise RuntimeError("registration failed after indexing")

    pipeline.documents.register = controlled
    port = free_port()
    app = build_app(pipeline, token=TOKEN, host="127.0.0.1", port=port)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, name="rag-ref", daemon=True)
    thread.start()
    for _ in range(400):
        if server.started:
            break
        await asyncio.sleep(0.05)
    assert server.started
    config = load_mcp_config(settings.config_dir / "mcp.yaml")
    config = config.model_copy(update={"servers": [s for s in config.servers if s.id == "rag"]})
    resolved = resolve(
        config,
        profile="default",
        allow_external=False,
        environ={"MCP_RAG_URL": f"http://127.0.0.1:{port}/mcp", "MCP_RAG_TOKEN": TOKEN},
    )
    client = MCPClient(resolved, hash_key=settings.content_hash_key, client_version="test")
    client.start()
    assert await client.wait_connected(20), client.health()
    backend = MCPDocumentBackend(ToolBroker(ToolRegistry(), GuardEngine(), mcp=client).transport, "rag")
    yield mode, backend
    await client.stop()
    server.should_exit = True
    await asyncio.to_thread(thread.join, 10)


_PDF: list[bytes] = []


def _pdf() -> bytes:
    """Built once, before any probe runs: drawing it is the test's own work on the loop."""
    if not _PDF:
        _PDF.append(pdf_bytes([PAGE]))
    return _PDF[0]


async def _upload(services, user: str):
    """Stores a one-page PDF as the gateway does and registers it; returns (scope, record, the
    registration's seconds)."""
    scope = DocumentScope.session(tenant_id="default", user_id=user, conversation_id=str(uuid.uuid4()))
    data = _pdf()
    key = f"test/{scope.conversations[0]}/{uuid.uuid4()}"
    await services.attachments.objects.put(key, data, "application/pdf")
    started = time.monotonic()
    record = await services.attachments.register(
        scope.owner, scope.conversations[0], name="الإجازات.pdf", mime="application/pdf", size=len(data),
        object_key=key,
    )  # fmt: skip
    return scope, record, time.monotonic() - started


async def _entered(mode, within_s: float = 10.0) -> None:
    """Bounded polling: until the server's `register` has the call (the PR #12 review, decision 10)."""
    end = time.monotonic() + within_s
    while not mode["entered"].is_set():
        assert time.monotonic() < end, "the server never received the registration"
        await asyncio.sleep(0.02)


async def _record(services, record_id: str, *, until: str, within_s: float):
    """Bounded polling on the agent's own record."""
    end = time.monotonic() + within_s
    while time.monotonic() < end:
        stored = await services.attachments.store.by_id(record_id)
        if stored.status == until:
            return stored
        await asyncio.sleep(0.1)
    raise AssertionError(f"the record {record_id} did not reach {until} in {within_s} s")


async def _server_status(backend, scope, document_id, *, until: str, within_s: float) -> str:
    deadline = time.monotonic() + within_s
    while time.monotonic() < deadline:
        status = await backend.status(document_id, scope)
        if status is not None and status.status == until:
            return status.status
        await asyncio.sleep(0.2)
    raise AssertionError(f"the server did not reach {until} for {document_id} in {within_s} s")


async def test_a_slow_registration_answers_pending_within_the_bound_and_the_file_is_indexed(services, rag):
    """Hung server: register held 25 s. The upload answers `pending` within 20.5 s; another user's
    upload meanwhile (the server not held for it) answers in its normal time; the loop never
    stalls; the registration goes on and the file ends indexed."""
    mode, backend = rag
    switched = with_backend(services, backend)
    assert switched.attachments.register_response_s == RESPONSE_BOUND_S
    _, quick, quick_alone = await _upload(switched, f"u-reg-q-{uuid.uuid4().hex[:6]}")
    assert quick.status == "pending"
    mode["held_s"] = REGISTER_HELD_S
    mode["entered"].clear()
    with LagProbe() as probe:
        slow = asyncio.create_task(_upload(switched, f"u-reg-a-{uuid.uuid4().hex[:6]}"))
        await _entered(mode)
        mode["held_s"] = 0.0  # held for A's call only: B's goes straight through
        _, other, other_took = await _upload(switched, f"u-reg-b-{uuid.uuid4().hex[:6]}")
        scope, record, took = await slow
    assert record.status == "pending", record
    assert RESPONSE_BOUND_S - 0.1 <= took <= RESPONSE_BOUND_S + 0.5, took
    assert other.status == "pending" and other_took < quick_alone + 1.0, (other_took, quick_alone)
    assert probe.worst < LAG_BOUND_S, probe.worst
    # The registration went on in the background: the server takes it at 25 s and indexes it.
    assert await _server_status(backend, scope, record.id, until="indexed", within_s=60) == "indexed"
    shown = await switched.attachments.get_with_status(scope.owner, scope.conversations[0], record.id)
    assert shown.status == "indexed", shown
    print(
        f"I5 hung register ({REGISTER_HELD_S:.0f} s): upload answered pending in {took:.2f} s "
        f"(bound {RESPONSE_BOUND_S + 0.5:.1f} s); another user's upload meanwhile {other_took:.2f} s "
        f"({quick_alone:.2f} s alone); loop lag max {probe.worst * 1000:.1f} ms; then indexed"
    )


async def test_a_failed_registration_ends_failed_and_its_chunks_are_never_returned(services, rag):
    """The server indexes the file and then fails the call: the file ends `failed` with
    `register_failed`, which a later status read never undoes, and search drops its chunks
    although the server returns them."""
    mode, backend = rag
    switched = with_backend(services, backend)
    mode["fail_after_index"] = True
    scope, record, took = await _upload(switched, f"u-reg-f-{uuid.uuid4().hex[:6]}")
    mode["fail_after_index"] = False
    assert (record.status, record.reason) == ("failed", "register_failed"), record
    assert took < RESPONSE_BOUND_S, took
    stored = await switched.attachments.store.by_id(record.id)
    assert (stored.status, stored.reason) == ("failed", "register_failed")
    # The server holds the file, indexed, and returns its chunks when asked directly.
    await _server_status(backend, scope, record.id, until="indexed", within_s=60)
    raw = await backend.search("إجازة الأمومة", scope, 8)
    assert any(hit.document_id == record.id for hit in raw)
    # The platform never does: not through the facade a capability's tool gets, not as a status.
    facade = switched.broker.documents.facade(
        "docs", ToolScope(tenant_id="default", user_id=scope.user_id, collections=scope.collections)
    )
    assert [c for c in await facade.search("إجازة الأمومة", 8) if c.hit.document_id == record.id] == []
    assert (await facade.status(record.id)).reason == "register_failed"
    shown = await switched.attachments.get_with_status(scope.owner, scope.conversations[0], record.id)
    assert (shown.status, shown.reason) == ("failed", "register_failed")
    await switched.attachments.store.remember_status(
        scope.owner, record.id, status="indexed", reason=None, pages=1, warnings=[]
    )
    assert (await switched.attachments.store.by_id(record.id)).reason == "register_failed"
    print(
        f"I5 failed register: upload answered failed register_failed in {took:.2f} s; chunks never returned"
    )  # noqa: E501


# ------------------------------------------------------------------ the PR #12 review, decisions 4, 6, 10


def _search_as(services, user: str):
    """A docs_search as the docs capability's tool makes it: the facade for `user`'s own new
    conversation (no files), through the broker's transport to the server."""
    scope = DocumentScope.session(tenant_id="default", user_id=user, conversation_id=str(uuid.uuid4()))
    facade = services.broker.documents.facade(
        "docs", ToolScope(tenant_id="default", user_id=user, collections=scope.collections)
    )

    async def search() -> tuple[list, float]:
        started = time.monotonic()
        found = await facade.search("إجازة الأمومة", 8)
        return found, time.monotonic() - started

    return search


async def test_two_users_uploading_against_a_slow_register_leave_another_users_search_its_slots(
    services, rag
):
    """The review's (4)3 setup: two users upload two files every 20 s against a server that holds
    `register` 25 s. Each registration counts toward its user's uploads in flight (2) until it ends,
    in the background too, so each user's third upload is refused at once, and background work
    takes at most half the server's slots: a third user's docs_search keeps finding a slot and
    answers in its normal time throughout."""
    mode, backend = rag
    switched = with_backend(services, backend)
    a, b, c = (f"u-reg-{who}-{uuid.uuid4().hex[:6]}" for who in "abc")
    search = _search_as(switched, c)
    _, alone = await search()
    mode["held_s"] = REGISTER_HELD_S
    searches: list[float] = []
    refused: list[str] = []
    started = time.monotonic()

    async def uploader(user: str) -> None:
        for _ in range(2):  # at 0 s and at 20 s
            begun = time.monotonic()
            outcomes = await asyncio.gather(
                *(_upload(switched, user) for _ in range(2)), return_exceptions=True
            )
            refused.extend(type(o).__name__ for o in outcomes if isinstance(o, BaseException))
            await asyncio.sleep(max(0.0, 20.0 - (time.monotonic() - begun)))

    try:
        with LagProbe() as probe:
            uploads = [asyncio.create_task(uploader(user)) for user in (a, b)]
            while not all(task.done() for task in uploads):
                found, took = await search()
                assert found == [], found
                searches.append(took)
                await asyncio.sleep(1.0)
            await asyncio.gather(*uploads)
    finally:
        mode["held_s"] = 0.0
    # At 20 s each user still had two registrations in flight (held 25 s): both uploads refused.
    assert refused.count("TooManyUploads") == 4, refused
    assert len(searches) >= 30 and max(searches) < alone + 1.0, (max(searches), alone)
    assert probe.worst < LAG_BOUND_S, probe.worst
    print(
        f"4 two users uploading every 20 s, register held {REGISTER_HELD_S:.0f} s: their second rounds "
        f"refused too_many_uploads ({len(refused)}); a third user's {len(searches)} searches over "
        f"{time.monotonic() - started:.0f} s, slowest {max(searches):.2f} s ({alone:.2f} s alone); loop "
        f"lag max {probe.worst * 1000:.1f} ms"
    )


async def test_a_users_third_concurrent_upload_is_refused_until_a_registration_ends(services, rag):
    mode, backend = rag
    switched = with_backend(services, backend)
    switched.attachments.register_response_s = 1.0
    user, other = f"u-reg-cap-{uuid.uuid4().hex[:6]}", f"u-reg-cap-o-{uuid.uuid4().hex[:6]}"
    mode["held_s"] = 4.0
    try:
        first = await asyncio.gather(_upload(switched, user), _upload(switched, user))
        assert [r.status for _, r, _ in first] == [
            "pending",
            "pending",
        ]  # answered, going on in the background
        started = time.monotonic()
        with pytest.raises(TooManyUploads):
            await _upload(switched, user)
        refused_s = time.monotonic() - started
        _, other_record, _ = await _upload(switched, other)  # another user is not refused
        assert other_record.status == "pending"
        end = time.monotonic() + 30  # bounded polling: the slots are released when the registrations end
        while switched.attachments.in_flight.in_flight("default", user) and time.monotonic() < end:
            await asyncio.sleep(0.05)
        assert switched.attachments.in_flight.in_flight("default", user) == 0
        for _, record, _ in first:  # they ended registered
            await _server_status(backend, DocumentScope.session(tenant_id="default", user_id=user,
                                                                conversation_id=record.conversation_id),
                                 record.id, until="indexed", within_s=60)  # fmt: skip
        mode["held_s"] = 0.0
        _, again, _ = await _upload(switched, user)
    finally:
        mode["held_s"] = 0.0
    assert refused_s < 0.5, refused_s
    assert again.status in ("pending", "indexed")
    print(f"4 a user's third concurrent upload refused too_many_uploads in {refused_s * 1000:.0f} ms; "
          "accepted again "
          "once its registrations ended")  # fmt: skip


async def test_a_failure_after_the_upload_answered_pending_ends_failed_and_returns_no_chunk(services, rag):
    mode, backend = rag
    switched = with_backend(services, backend)
    switched.attachments.register_response_s = 0.5
    mode["held_s"], mode["fail_after_index"] = 2.0, True
    try:
        scope, record, took = await _upload(switched, f"u-reg-late-{uuid.uuid4().hex[:6]}")
        assert record.status == "pending" and took < 1.5, (record, took)
        failed = await _record(switched, record.id, until="failed", within_s=30)
    finally:
        mode["held_s"], mode["fail_after_index"] = 0.0, False
    assert failed.reason == "register_failed"
    await _server_status(backend, scope, record.id, until="indexed", within_s=60)
    assert any(hit.document_id == record.id for hit in await backend.search("إجازة الأمومة", scope, 8))
    facade = switched.broker.documents.facade(
        "docs", ToolScope(tenant_id="default", user_id=scope.user_id, collections=scope.collections)
    )
    assert [c for c in await facade.search("إجازة الأمومة", 8) if c.hit.document_id == record.id] == []
    print(f"10 a registration failing after its upload answered pending ({took:.2f} s): "
          "failed register_failed, "
          "its chunks never returned")  # fmt: skip


@pytest.fixture
async def ops_server(settings):
    """A document server that trickles or floods `register` on demand (tests/fixtures/doc_ops_mcp.py),
    timeout_s 3, max_concurrency 4, a 256 KiB result cap."""
    server = DocumentOpsMCPServer()
    server.start()
    client, backend = await connected(
        server, hash_key=settings.content_hash_key, timeout_s=3.0, max_concurrency=4
    )
    yield server, backend
    await client.stop()
    server.stop()


@pytest.mark.parametrize("kind", ["trickle", "flood"])
async def test_a_trickling_or_flooding_register_ends_failed_within_its_one_deadline(
    services, ops_server, kind
):
    """A background registration has one deadline, timeout_s (3 s) for its slot wait and its attempt;
    a trickling register is cut there, a flooding one at its byte cap; either ends the file failed
    register_failed, while another user's search on the same server answers in its normal time."""
    server, backend = ops_server
    switched = with_backend(services, backend)
    switched.attachments.register_response_s = 1.0
    search = _search_as(switched, f"u-reg-ops-o-{uuid.uuid4().hex[:6]}")
    _, alone = await search()
    server.mode["register"] = kind
    try:
        with LagProbe() as probe:
            upload = asyncio.create_task(_upload(switched, f"u-reg-ops-{uuid.uuid4().hex[:6]}"))
            while not server.calls["register"]:  # bounded by the upload's own 1 s answer
                await asyncio.sleep(0.02)
            _, other_took = await search()
            _, record, took = await upload
            ended = time.monotonic()
            failed = await _record(switched, record.id, until="failed", within_s=10)
            failed_s = took + (time.monotonic() - ended)
    finally:
        server.mode["register"] = "ok"
    assert failed.reason == "register_failed"
    if kind == "trickle":
        assert record.status == "pending" and 0.9 <= took <= 1.5, (record, took)
        assert failed_s <= 3.0 + 1.0, failed_s  # one deadline of timeout_s, and the record's poll
    else:
        assert record.status == "failed" and took < 1.0, (record, took)  # refused at its byte cap, unparsed
    assert other_took < alone + 1.0, (other_took, alone)
    assert probe.worst < LAG_BOUND_S, probe.worst
    print(f"10 register {kind}: upload answered {record.status} in {took:.2f} s, failed register_failed by "
          f"{failed_s:.2f} s (timeout_s 3); another user's search {other_took:.2f} s ({alone:.2f} s alone); "
          f"loop lag max {probe.worst * 1000:.1f} ms")  # fmt: skip


async def test_on_the_internal_backend_the_upload_answers_what_the_record_holds(services):
    """Decision 6 (ADR-0265): the internal backend's push lands and then its call fails. Whichever
    side wins the race, the upload answers the record as stored after it, and a file whose
    registration failed is never returned by search or fetch."""
    internal = services.backends["internal"]

    class PushThenFail:
        name = "internal"

        def __init__(self, indexed_first: bool):
            self.indexed_first = indexed_first

        async def register(self, stored):
            await internal.register(stored)  # the RPUSH lands
            if self.indexed_first:  # the worker moves the record on before the failure is marked
                async with services.db.connection() as conn:
                    assert await services.attachments.store.mark_indexed(
                        stored.id, pages=1, chunks=0, conn=conn
                    )
            raise RuntimeError("connection reset after the push")

        def __getattr__(self, name):
            return getattr(internal, name)

    won = with_backend(services, PushThenFail(indexed_first=True))
    _, record, _ = await _upload(won, f"u-reg-race-a-{uuid.uuid4().hex[:6]}")
    assert record.status == "indexed" and record.reason is None, record  # the stored state, not "failed"
    assert (await services.attachments.store.by_id(record.id)).status == "indexed"

    lost = with_backend(services, PushThenFail(indexed_first=False))
    scope, record, _ = await _upload(lost, f"u-reg-race-b-{uuid.uuid4().hex[:6]}")
    assert (record.status, record.reason) == ("failed", "register_failed"), record
    async with services.db.connection() as conn:  # the worker comes later: the record stays failed
        assert not await services.attachments.store.mark_indexed(record.id, pages=1, chunks=0, conn=conn)
    assert (await services.attachments.store.by_id(record.id)).reason == "register_failed"
