"""One user or one server never stalls other users' chats (fix session 2026-10-19, PR #10 review
round 2, C1 and C2; ADR-0244 to ADR-0246).

1. A caller's cancellation never decides the breaker: a cancelled trial is released without a
   verdict; the server's own outcome decides (success closes, its timeout_s reopens).
2. A status read past its 2 s bound is not cancelled: it ends in the background and keeps what it
   read; one read per attachment is in flight, later list reads join it.
3. One session per call in flight: a server that holds one call's answer never delays another
   call; a cancelled or timed-out call closes its session, so no request stays open at the server
   and requests open there never exceed max_concurrency; server-initiated requests are dropped.

The slow server runs in a thread of its own (tests/fixtures/slow_mcp.py) and counts the requests
open at it. Timings are printed for docs/PROGRESS.md (rule 11)."""

import asyncio
import contextlib
import logging
import time
import uuid
from datetime import UTC, datetime

import pytest

from app.api.runner import TurnRunner
from app.api.schemas import TurnRequest
from app.attachments.service import AttachmentService
from app.attachments.store import AttachmentRecord
from app.guardrails.engine import GuardEngine
from app.identity import Principal
from app.mcp.client import Breaker, MCPClient, schema_hash
from app.mcp.config import MCPConfig, resolve
from app.mcp.tools import register_mcp_tools
from app.mcp.transport import REQUEST_DROPPED
from app.rag.backend import DocumentScope, DocumentStatus
from app.rag.types import Owner
from app.tools.broker import ToolBroker
from app.tools.registry import ToolRegistry
from app.tools.spec import ToolFailure
from tests.fixtures.slow_mcp import ASK_HOLD_S, SCHEMAS, SlowMCPServer

pytestmark = pytest.mark.integration

LABEL = {"ar": "أداة اختبار", "en": "Test tool"}
STATUS_BOUND_S = 2.0  # config/docs.yaml status_timeout_s


def _config(*, timeout_s: float = 10.0, max_concurrency: int = 8) -> MCPConfig:
    """The slow server: `status` a transport operation, its other tools broker tools."""
    tools = [
        {"name": name, "remote_name": name, "risk": "read", "label": LABEL,
         "input_schema_sha256": schema_hash(schema)}
        for name, schema in SCHEMAS.items() if name != "status"
    ] + [
        {"name": "status", "remote_name": "status", "class": "transport", "risk": "read", "label": LABEL,
         "input_schema_sha256": schema_hash(SCHEMAS["slow"])}
    ]  # fmt: skip
    return MCPConfig.model_validate(
        {
            "servers": [
                {
                    "id": "fx",
                    "url_env": "URL",
                    "internal": True,
                    "profiles": ["default"],
                    "timeout_s": timeout_s,
                    "max_concurrency": max_concurrency,
                    "tools": tools,
                }
            ]
        }  # fmt: skip
    )


async def _client(server: SlowMCPServer, **limits) -> MCPClient:
    client = MCPClient(
        resolve(_config(**limits), profile="default", allow_external=False, environ={"URL": server.url}),
        hash_key="test-content-hash-key",
        client_version="test",
    )
    client.start()
    assert await client.wait_connected(10), client.health()
    assert client.servers["fx"].disabled == {}
    return client


def _broker(client: MCPClient) -> ToolBroker:
    registry = ToolRegistry()
    register_mcp_tools(registry, client)
    return ToolBroker(registry, GuardEngine(), {"cap": ["fx.slow", "fx.quick", "fx.ask"]}, {"cap": []})


def _scope(user: str) -> DocumentScope:
    return DocumentScope.session(tenant_id="default", user_id=user, conversation_id=str(uuid.uuid4()))


async def _call(broker: ToolBroker, name: str, user: str, args: dict | None = None):
    started = time.monotonic()
    result = await broker.execute(
        name,
        args or {},
        caller="cap",
        principal=Principal(user_id=user, permissions=("cap:cap",)),
        conversation_id="c-sessions",
    )
    return result, time.monotonic() - started


def _half_open(connection) -> list[float]:
    """Opens the breaker on a clock the test moves, then moves it 30 s: the next call is the trial."""
    now = [1000.0]
    connection.breaker = Breaker(clock=lambda: now[0])
    for _ in range(5):
        connection.breaker.failure()
    now[0] += 30
    assert connection.breaker.state == "half_open"
    return now


async def _settled(server: SlowMCPServer, within_s: float = 2.0) -> float:
    """Seconds until no request is open at the server; fails past `within_s`."""
    started = time.monotonic()
    while server.open_requests:
        assert time.monotonic() - started < within_s, server.open_requests
        await asyncio.sleep(0.02)
    return time.monotonic() - started


class SlowStatusBackend:
    """A document backend whose status is one `broker.transport` call, as MCPDocumentBackend's is."""

    name = "mcp"

    def __init__(self, transport, seconds: float):
        self._transport = transport
        self.seconds = seconds

    async def status(self, document_id, scope):
        await self._transport("fx", "status", {"seconds": self.seconds}, scope)
        return DocumentStatus(status="indexed", reason=None, pages=1, warnings=[])


class Records:
    """The attachment store's reads and its `remember_status`, in memory."""

    def __init__(self, owner: Owner, count: int):
        now = datetime.now(UTC)
        self.records = [
            AttachmentRecord(
                id=f"a{i}-{uuid.uuid4().hex[:6]}", conversation_id="c1", tenant_id=owner.tenant_id,
                user_id=owner.user_id, name=f"{i}.txt", mime="text/plain", kind="txt", size=4,
                object_key=f"k{i}", collection="session:c1", backend="mcp", status="pending",
                created_at=now, updated_at=now,
            )
            for i in range(count)
        ]  # fmt: skip
        self.remembered: dict[str, str] = {}

    async def list_for(self, owner, conversation_id):
        return [r.model_copy(update={"status": self.remembered.get(r.id, r.status)}) for r in self.records]

    async def remember_status(self, owner, attachment_id, *, status, reason, pages, warnings):
        self.remembered[attachment_id] = status


def _attachments(backend, records: Records) -> AttachmentService:
    return AttachmentService(
        db=None, store=records, objects=None, documents=backend, limits=None,
        backends={"mcp": backend}, status_timeout_s=STATUS_BOUND_S,
    )  # fmt: skip


@pytest.fixture
def slow_server():
    server = SlowMCPServer()
    server.start()
    yield server
    server.stop()


# ------------------------------------------------------------------ 1. the breaker (C1)


@pytest.mark.parametrize("ending", ["status_bound", "cancelled"])
async def test_a_status_read_past_its_bound_never_reopens_the_breaker(slow_server, ending):
    """(a) The reviewer's case: the server answers in 2.5 s with a 10 s timeout and the breaker is
    half open. User A's file list returns at its 2 s bound, and the breaker is not reopened.

    - status_bound: A's read is not cancelled (item 2); it is the trial, ends at 2.5 s and closes
      the breaker; user B's next call reaches the server.
    - cancelled: the same trial cancelled by its caller at 2 s (a capability time limit, a client
      gone) is released without a verdict; user B's next call is the trial, reaches the server and
      closes it."""
    client = await _client(slow_server, timeout_s=10.0)
    connection = client.servers["fx"]
    broker = ToolBroker(ToolRegistry(), GuardEngine(), mcp=client)
    backend = SlowStatusBackend(broker.transport, 2.5)
    owner = Owner(tenant_id="default", user_id="user-a")
    records = Records(owner, 1)
    attachments = _attachments(backend, records)
    try:
        _half_open(connection)
        started = time.monotonic()
        if ending == "status_bound":
            listed = await attachments.with_status(owner, "c1")
            assert [r.status for r in listed] == ["pending"]  # the last known status
        else:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(backend.status("a", _scope("user-a")), STATUS_BOUND_S)
        returned = time.monotonic() - started
        assert STATUS_BOUND_S - 0.05 < returned < STATUS_BOUND_S + 0.4, returned
        assert connection.breaker.state == "half_open", connection.breaker.state
        if ending == "status_bound":
            assert connection.breaker.trial_in_progress  # A's read goes on, and is the trial
            for _ in range(100):
                if not connection.breaker.trial_in_progress:
                    break
                await asyncio.sleep(0.02)
            assert connection.breaker.state == "closed"  # the server's own answer decided
            assert list(records.remembered.values()) == ["indexed"]
        else:
            assert not connection.breaker.trial_in_progress  # released, no verdict
        before = slow_server.calls["status"]
        b_started = time.monotonic()
        await broker.transport("fx", "status", {"seconds": 0}, _scope("user-b"))
        b_took = time.monotonic() - b_started
        assert slow_server.calls["status"] == before + 1  # B's call reached the server
        assert connection.breaker.state == "closed" and connection.breaker.failures == 0
        print(f"C1 (a) {ending}: A returned in {returned:.2f} s, breaker half open; "
              f"B's call reached the server in {b_took:.2f} s, breaker closed")  # fmt: skip
    finally:
        await client.stop()


async def test_a_trial_cancelled_repeatedly_never_wedges_and_never_reopens(slow_server):
    """(b) Ten trials, each cancelled by its caller while the server holds it: each is released,
    the breaker stays half open, the next call is the next trial, and none leaves a request open.
    Then a trial the server answers closes it."""
    client = await _client(slow_server, timeout_s=10.0)
    connection = client.servers["fx"]
    slow, quick = connection.entry.tool("slow"), connection.entry.tool("quick")
    try:
        _half_open(connection)
        started = time.monotonic()
        for i in range(10):
            task = asyncio.create_task(connection.call(slow, {"seconds": 5}))
            await asyncio.sleep(0.2)
            assert connection.breaker.trial_in_progress, i
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            assert connection.breaker.state == "half_open" and not connection.breaker.trial_in_progress, i
        cancelled = time.monotonic() - started
        settled = await _settled(slow_server)
        assert slow_server.peak <= connection.entry.max_concurrency
        assert (await connection.call(quick, {})).data == {"ok": True}
        assert connection.breaker.state == "closed" and connection.breaker.failures == 0
        print(f"C1 (b): 10 cancelled trials in {cancelled:.2f} s, never reopened; no request open "
              f"{settled:.2f} s later; a trial then closed the breaker")  # fmt: skip
    finally:
        await client.stop()


async def test_a_trial_past_the_servers_timeout_still_reopens(slow_server):
    """(c) The call's own timeout_s is the server's outcome: the trial reopens the breaker."""
    client = await _client(slow_server, timeout_s=1.0)
    connection = client.servers["fx"]
    try:
        _half_open(connection)
        started = time.monotonic()
        with pytest.raises(ToolFailure) as failed:
            await connection.call(connection.entry.tool("slow"), {"seconds": 3})
        took = time.monotonic() - started
        assert failed.value.code == "tool_timeout"
        assert connection.breaker.state == "open" and not connection.breaker.trial_in_progress
        assert took < 1.0 + 0.5, took
        await _settled(slow_server)
        print(f"C1 (c): the trial timed out in {took:.2f} s (timeout_s 1.0) and reopened the breaker")
    finally:
        await client.stop()


# ------------------------------------------------------------------ 2. status reads (C1)


async def _general_turn_s(services) -> float:
    principal = Principal(user_id=f"u-c1-{uuid.uuid4().hex[:6]}", permissions=("cap:general",))
    request = TurnRequest(
        conversation_id=str(uuid.uuid4()), principal=principal, message="ما عاصمة المملكة؟", locale="ar"
    )
    started = time.monotonic()
    events = [e async for e in TurnRunner(services).stream(request, f"req-{uuid.uuid4().hex[:6]}")]
    assert events[-1].type == "done"
    return time.monotonic() - started


async def test_repeated_list_reads_join_one_read_per_file_and_never_stall_another_user(
    slow_server, services, litellm
):
    """A server answering status in 5 s; a user reading a 10-file list 20 times (two tabs, ten
    rounds) holds at most 10 requests at the server, each list returns at its bound, the reads'
    results update the cached status, and another user's general turn completes in its normal
    time."""
    baseline = min([await _general_turn_s(services) for _ in range(2)])
    client = await _client(slow_server, timeout_s=10.0, max_concurrency=16)
    broker = ToolBroker(ToolRegistry(), GuardEngine(), mcp=client)
    owner = Owner(tenant_id="default", user_id="user-a")
    records = Records(owner, 10)
    attachments = _attachments(SlowStatusBackend(broker.transport, 5.0), records)
    try:
        slow_server.reset_peak()
        lists: list[float] = []

        async def read_list() -> None:
            started = time.monotonic()
            await attachments.with_status(owner, "c1")
            lists.append(time.monotonic() - started)

        async def reads() -> None:
            for _ in range(10):
                await asyncio.gather(read_list(), read_list())

        reading = asyncio.create_task(reads())
        await asyncio.sleep(3.0)
        during = await _general_turn_s(services)
        await reading
        assert len(lists) == 20
        assert max(lists) < STATUS_BOUND_S + 0.5, max(lists)
        assert slow_server.peak <= 10, slow_server.peak
        assert set(records.remembered.values()) == {"indexed"} and len(records.remembered) == 10
        assert during < baseline + 1.0, (during, baseline)
        print(f"C1 item 2: 20 list reads, slowest {max(lists):.2f} s (bound {STATUS_BOUND_S} s); "
              f"peak requests at the server {slow_server.peak} (10 files); status calls "
              f"{slow_server.calls['status']}; another user's general turn {during:.2f} s "
              f"(normal {baseline:.2f} s)")  # fmt: skip
    finally:
        await client.stop()


# ------------------------------------------------------------------ 3. one session per call (C2)


async def test_a_server_holding_one_users_answer_never_delays_another_users_call(slow_server, caplog):
    """(a) The reviewer's case: user A's call makes the server send a sampling request on A's stream,
    and the server holds A's answer (and would hold any reply to its request). User B's quick call
    on the same server takes under 0.5 s; the request is dropped unanswered and logged by its code."""
    caplog.set_level(logging.WARNING, logger="app.mcp.transport")
    client = await _client(slow_server, timeout_s=10.0)
    broker = _broker(client)
    try:
        _, alone = await _call(broker, "fx.quick", "user-b")
        a = asyncio.create_task(_call(broker, "fx.ask", "user-a"))
        await asyncio.sleep(0.5)
        assert not a.done() and slow_server.calls["ask"] == 1
        b, b_took = await _call(broker, "fx.quick", "user-b")
        assert b.status == "ok" and b_took < 0.5, b_took
        a_result, a_took = await a
        assert a_result.status == "ok" and a_result.data == {"asked": True}
        assert ASK_HOLD_S - 0.1 < a_took < ASK_HOLD_S + 1.0, a_took
        assert slow_server.replies == 0  # nothing was answered
        dropped = [r for r in caplog.records if r.name == "app.mcp.transport"]
        assert [r.getMessage() for r in dropped] == [REQUEST_DROPPED]
        print(f"C2 (a): B's call {b_took * 1000:.0f} ms while the server held A's answer "
              f"(alone {alone * 1000:.0f} ms); A's call {a_took:.2f} s; "
              "the server's request dropped")  # fmt: skip
    finally:
        await client.stop()


@pytest.mark.parametrize("ending", ["cancelled", "timed_out"])
async def test_calls_that_end_early_leave_no_request_open_at_the_server(slow_server, ending):
    """(b) Twelve calls in three waves on a server whose max_concurrency is 4, each holding the
    server 5 s, each cancelled by its caller after 0.3 s (or cut by a 1 s timeout_s): requests open
    at the server never exceed 4 and none is left open once the calls have ended; then another
    user's call is answered at once."""
    timeout_s = 10.0 if ending == "cancelled" else 1.0
    client = await _client(slow_server, timeout_s=timeout_s, max_concurrency=4)
    connection = client.servers["fx"]
    slow, quick = connection.entry.tool("slow"), connection.entry.tool("quick")
    try:
        slow_server.reset_peak()
        started = time.monotonic()
        settled = []
        for _ in range(3):
            calls = [asyncio.ensure_future(connection.call(slow, {"seconds": 5})) for _ in range(4)]
            if ending == "cancelled":
                await asyncio.sleep(0.3)
                for call in calls:
                    call.cancel()
            outcomes = await asyncio.gather(*calls, return_exceptions=True)
            if ending == "cancelled":
                assert all(isinstance(o, asyncio.CancelledError) for o in outcomes), outcomes
            else:
                assert {getattr(o, "code", None) for o in outcomes} == {"tool_timeout"}, outcomes
                connection.breaker.success()  # timeouts open it; that is not what is tested here
            settled.append(await _settled(slow_server))
        took = time.monotonic() - started
        assert slow_server.peak <= 4, slow_server.peak
        assert slow_server.calls["slow"] == 12
        quick_started = time.monotonic()
        assert (await connection.call(quick, {})).data == {"ok": True}
        quick_took = time.monotonic() - quick_started
        assert quick_took < 0.5, quick_took
        print(f"C2 (b) {ending}: 12 calls in {took:.2f} s, peak requests at the server "
              f"{slow_server.peak} (max_concurrency 4), none open {max(settled):.2f} s after each wave "
              f"at worst; a quick call then {quick_took * 1000:.0f} ms")  # fmt: skip
    finally:
        await client.stop()
