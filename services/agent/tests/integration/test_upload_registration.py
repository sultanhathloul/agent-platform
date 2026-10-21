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

from app.guardrails.engine import GuardEngine
from app.mcp.client import MCPClient
from app.mcp.config import load_mcp_config, resolve
from app.rag.backend import DocumentScope
from app.rag.mcp import MCPDocumentBackend
from app.tools.broker import ToolBroker
from app.tools.registry import ToolRegistry
from app.tools.spec import ToolScope
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
    mode: dict[str, object] = {"held_s": 0.0, "fail_after_index": False}
    register = pipeline.documents.register

    async def controlled(fields):
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
    with LagProbe() as probe:
        slow = asyncio.create_task(_upload(switched, f"u-reg-a-{uuid.uuid4().hex[:6]}"))
        await asyncio.sleep(1.0)
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
    print(f"I5 failed register: upload answered failed register_failed in {took:.2f} s; chunks never returned")  # noqa: E501
