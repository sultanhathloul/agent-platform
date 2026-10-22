"""MCP calls nested under their caller's deadline (timeout budget PR, scopes E and F, I6; ADR-0259).

The findings (docs/BACKLOG.md): the broker's bound (timeout_s + 5) was shorter than what the client
could take with a full queue (a wait of timeout_s, then an attempt of timeout_s + 2), so the
broker's tool_timeout fired before the client's tool_busy; and the SDK's read timeout equalled the
transport's deadline, so which fired first was a race. Now: the broker's bound is
min(2 × timeout_s + 2, what remains); the wait for a slot is at most min(timeout_s, remaining −
timeout_s − 1), and a call with no wait left and no free slot is tool_busy at once, the breaker
untouched; one attempt deadline, min(timeout_s, remaining − 0.5), covers the session, the request
and the I9 re-send with its wait for the next generation; the caller's deadline ending an attempt
is no verdict on the server; the SDK's read timeout is timeout_s + 1.

The servers run in threads of their own (tests/fixtures/slow_mcp.py, raw_mcp.py), so their work
never shows in the event-loop lag measured here. Timings are printed for docs/PROGRESS.md."""

import asyncio
import time
from datetime import timedelta

import pytest

from app.guardrails.engine import GuardEngine
from app.identity import Principal
from app.mcp.client import MCPClient, schema_hash
from app.mcp.config import MCPConfig, resolve
from app.mcp.tools import register_mcp_tools
from app.resilience import deadline
from app.tools.broker import ToolBroker
from app.tools.registry import ToolRegistry
from tests.fixtures.raw_mcp import TOOLS as RAW_TOOLS
from tests.fixtures.raw_mcp import RawMCPServer
from tests.fixtures.slow_mcp import SCHEMAS, RestartingMCPServer, SlowMCPServer
from tests.fixtures.timing import LagProbe

pytestmark = pytest.mark.integration

LABEL = {"ar": "أداة اختبار", "en": "Test tool"}
LAG_BOUND_S = 0.1
EDGE_S = 0.5
EMPTY = {"type": "object", "properties": {}}


def _config(schemas: dict, *, timeout_s: float, max_concurrency: int = 8) -> MCPConfig:
    tools = [
        {"name": name, "remote_name": name, "risk": "read", "label": LABEL,
         "input_schema_sha256": schema_hash(schema)}
        for name, schema in schemas.items()
    ]  # fmt: skip
    return MCPConfig.model_validate(
        {"servers": [{"id": "fx", "url_env": "URL", "internal": True, "profiles": ["default"],
                      "timeout_s": timeout_s, "max_concurrency": max_concurrency, "tools": tools}]}
    )  # fmt: skip


async def _client(url: str, schemas: dict, **limits) -> MCPClient:
    client = MCPClient(
        resolve(_config(schemas, **limits), profile="default", allow_external=False, environ={"URL": url}),
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
    names = [f"fx.{name}" for name in client.servers["fx"].verified]
    return ToolBroker(registry, GuardEngine(), {"cap": names}, {"cap": []})


async def _call(broker: ToolBroker, name: str, user: str, args: dict | None = None):
    started = time.monotonic()
    result = await broker.execute(
        f"fx.{name}", args or {}, caller="cap", principal=Principal(user_id=user), conversation_id="c-bounds"
    )
    return result, time.monotonic() - started


def _counting(connection) -> list[int]:
    """Every failure the breaker is told of (a later success would reset the count)."""
    counted: list[int] = []
    failure = connection.breaker.failure

    def counting() -> None:
        counted.append(1)
        failure()

    connection.breaker.failure = counting
    return counted


async def _open(server, count: int, within_s: float = 3.0) -> None:
    """Bounded polling: until `count` requests are open at the server (the PR #12 review, decision 10)."""
    started = time.monotonic()
    while getattr(server, "open_requests", 0) < count:
        assert time.monotonic() - started < within_s, server.open_requests
        await asyncio.sleep(0.02)


async def _settled(server, within_s: float = 2.0) -> float:
    started = time.monotonic()
    while getattr(server, "open_requests", 0) or getattr(server, "open_streams", 0):
        assert time.monotonic() - started < within_s
        await asyncio.sleep(0.02)
    return time.monotonic() - started


@pytest.fixture
def slow_server():
    server = SlowMCPServer()
    server.start()
    yield server
    server.stop()


@pytest.fixture
def raw_server():
    server = RawMCPServer()
    server.start()
    yield server
    server.stop()


@pytest.mark.parametrize("kind", ["hung", "trickle"])
async def test_a_call_ends_by_its_callers_deadline_and_never_decides_the_breaker(
    slow_server, raw_server, kind
):
    """A server holding the call (30 s) or trickling a byte every 2 s, timeout_s 10, inside a caller
    with 1.5 s left: the attempt ends at 1.0 s (1.5 − 0.5) as tool_timeout, no failure is counted,
    no request stays open, and another user's quick call on the same server takes its usual time."""
    if kind == "hung":
        server, schemas, name, args = slow_server, SCHEMAS, "slow", {"seconds": 30}
    else:
        server, schemas, name, args = raw_server, {tool: EMPTY for tool in RAW_TOOLS}, "trickle", {}
    client = await _client(server.url, schemas, timeout_s=10.0)
    connection = client.servers["fx"]
    counted = _counting(connection)
    broker = _broker(client)
    try:
        _, alone = await _call(broker, "quick", "user-b")

        async def bounded():
            with deadline.within(1.5):
                return await _call(broker, name, "user-a", args)

        with LagProbe() as probe:
            mine = asyncio.create_task(bounded())
            if kind == "hung":
                await _open(server, 1)
            else:
                while not server.open_streams:  # bounded by the test's own 1.5 s: the stream opens first
                    await asyncio.sleep(0.02)
            other, other_took = await _call(broker, "quick", "user-b")
            result, took = await mine
        assert (result.status, result.error) == ("error", "tool_timeout"), result
        assert took <= 1.0 + EDGE_S, took
        assert other.status == "ok" and other_took < alone + 0.5, (other_took, alone)
        assert probe.worst < LAG_BOUND_S, probe.worst
        assert not counted and connection.breaker.state == "closed"
        closed = await _settled(server)
        print(f"I6 {kind} inside a 1.5 s caller (timeout_s 10): tool_timeout at {took:.2f} s, no breaker "
              f"verdict; another user's call {other_took * 1000:.0f} ms ({alone * 1000:.0f} ms alone); "
              f"loop lag max {probe.worst * 1000:.1f} ms; request closed {closed:.2f} s after")  # fmt: skip
    finally:
        await client.stop()


async def test_a_full_queue_is_busy_at_once_when_no_wait_is_left(slow_server):
    """max_concurrency 1, held 3 s. With 5 s left and timeout_s 10 there is no wait left
    (min(10, 5 − 11) < 0): tool_busy at once, the breaker untouched. With 30 s left the call waits
    for the slot (min(10, 30 − 11)) and succeeds."""
    client = await _client(slow_server.url, SCHEMAS, timeout_s=10.0, max_concurrency=1)
    connection = client.servers["fx"]
    counted = _counting(connection)
    broker = _broker(client)
    try:
        _, alone = await _call(broker, "quick", "user-b")  # the baseline: a call that takes its slot at once
        holder = asyncio.create_task(_call(broker, "slow", "user-a", {"seconds": 3}))
        await _open(slow_server, 1)
        with deadline.within(5.0):
            busy, busy_took = await _call(broker, "quick", "user-b")
        assert (busy.status, busy.error) == ("error", "tool_busy"), busy
        assert busy_took < alone + 0.2, (busy_took, alone)  # refused at once, as fast as a free call
        with deadline.within(30.0):
            waited, waited_took = await _call(broker, "quick", "user-c")
        assert waited.status == "ok" and 2.0 <= waited_took <= 3.5, (waited, waited_took)
        assert (await holder)[0].status == "ok"
        assert not counted and connection.breaker.state == "closed"
        print(f"I6 full queue: tool_busy in {busy_took * 1000:.1f} ms ({alone * 1000:.1f} ms a free call) "
              "with no wait left; "
              f"with 30 s left the call waited {waited_took:.2f} s for the slot")  # fmt: skip
    finally:
        await client.stop()


async def test_the_resend_waits_for_the_next_generation_only_within_its_attempt():
    """I9 inside one attempt deadline: the server restarts and holds every initialize 5 s; the
    call's re-send waits for the next generation only until its attempt deadline (timeout_s 2) and
    ends tool_timeout without a verdict on the server."""
    server = RestartingMCPServer()
    server.start()
    client = await _client(server.url, SCHEMAS, timeout_s=2.0)
    connection = client.servers["fx"]
    counted = _counting(connection)
    broker = _broker(client)
    try:
        assert (await _call(broker, "quick", "user-a"))[0].status == "ok"
        server.restart()
        server.init_hold_s = 5.0
        result, took = await _call(broker, "quick", "user-a")
        assert (result.status, result.error) == ("error", "tool_timeout"), result
        assert 1.5 <= took <= 2.0 + EDGE_S, took
        assert not counted and connection.breaker.state == "closed"
        print(f"I6 re-send: waited for the next generation until the attempt deadline, tool_timeout at "
              f"{took:.2f} s (timeout_s 2), no breaker verdict")  # fmt: skip
    finally:
        server.init_hold_s = 0.0
        await client.stop()
        server.stop()


async def test_the_sdks_read_timeout_is_past_the_transports_deadline(slow_server):
    """F: the transport's wall-clock deadline (timeout_s) always fires before the SDK's own."""
    client = await _client(slow_server.url, SCHEMAS, timeout_s=3.0)
    try:
        sessions = [pooled.session for pooled in client.servers["fx"]._idle if pooled.session is not None]
        assert sessions
        for session in sessions:
            assert session._session_read_timeout_seconds == timedelta(seconds=4.0)
        assert client.servers["fx"]._idle[0].transport.timeout_s == 3.0
    finally:
        await client.stop()


async def test_background_work_takes_at_most_half_the_slots_and_keeps_its_own_timeout(slow_server):
    """Decision 4 (ADR-0265): calls in a detached context (registrations past the upload's answer,
    status reads past their bound) take one of max_concurrency // 2 background slots first, so a
    caller's call always finds a slot. Review round 2, finding (1) (ADR-0274): a background call's
    slot wait is bounded on its own, as a caller's is (ADR-0259), and once it has a slot its attempt
    has the full timeout_s; a server that holds it that long still counts."""
    client = await _client(slow_server.url, SCHEMAS, timeout_s=4.0, max_concurrency=4)
    connection = client.servers["fx"]
    counted = _counting(connection)
    broker = _broker(client)
    loop = asyncio.get_running_loop()

    def background(seconds: float):
        return loop.create_task(
            _call(broker, "slow", f"user-bg-{seconds}", {"seconds": seconds}),
            context=deadline.detached_context(),
        )

    try:
        _, alone = await _call(broker, "quick", "user-c")
        slow_server.reset_peak()
        first = [background(30) for _ in range(2)]  # hung: each takes a slot at once
        await _open(slow_server, 2)
        await asyncio.sleep(1.0)
        queued = [background(30) for _ in range(4)]  # wait up to timeout_s (4 s) for a slot, ~3 s
        peak_background = slow_server.peak
        with deadline.within(20.0):
            mine, mine_took = await _call(broker, "quick", "user-c")
        outcomes = await asyncio.gather(*first, *queued)
        assert peak_background == 2, peak_background  # max_concurrency 4 // 2
        assert mine.status == "ok" and mine_took < alone + 0.5, (mine_took, alone)
        # The first two timed out at timeout_s (4 s). Two of the queued took their slots when those
        # ended and had their own full 4 s; the other two found none within their wait and were busy.
        busy = [took for r, took in outcomes if r.error == "tool_busy"]
        timed_out = [took for r, took in outcomes if r.error == "tool_timeout"]
        assert len(timed_out) == 4 and len(busy) == 2, [r.error for r, _ in outcomes]
        assert all(took <= 4.0 + EDGE_S for took in busy), busy
        # The queued two waited about 3 s, then had their own full 4 s (not what was left of 4 s).
        waited = sorted(timed_out)[2:]
        assert all(3.0 + 4.0 - EDGE_S <= took <= 4.0 + 4.0 + EDGE_S for took in waited), timed_out
        assert len(counted) == 4  # each held its full timeout_s by the server: the server's outcome
        print(f"4 background slots: peak {peak_background} of 4 at the server, a caller's call "
              f"{mine_took * 1000:.0f} ms ({alone * 1000:.0f} ms alone); background calls timed out at "
              f"{', '.join(f'{t:.2f}' for t in sorted(timed_out))} s (timeout_s 4), busy at "
              f"{', '.join(f'{t:.2f}' for t in sorted(busy))} s")  # fmt: skip
    finally:
        await client.stop()
