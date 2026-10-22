"""A slow, flooding, trickling, failing or garbled MCP server never stalls the agent (G7) and never
puts its text in a log, a span or an audit entry (G8); the breaker's trial always resolves (fix
session 2026-10-18; ADR-0234, ADR-0236, ADR-0237).

The misbehaving servers run in a thread of their own (tests/fixtures/raw_mcp.py), so the event-loop
lag a probe task measures here is the agent's alone."""

import asyncio
import json
import logging
import time
import uuid

import pytest

from app.api.runner import TurnRunner
from app.api.schemas import TurnRequest
from app.guardrails.checkpoints import Checkpoint
from app.guardrails.engine import GuardEngine
from app.identity import Principal
from app.mcp.client import Breaker, MCPClient
from app.mcp.config import MCPConfig, resolve
from app.mcp.tools import register_mcp_tools
from app.observability.logs import quiet_libraries
from app.tools.broker import ToolBroker
from app.tools.registry import ToolRegistry
from app.tools.spec import ToolFailure, ToolOutput
from tests.fixtures.mcp_server import FixtureServer, tool_hashes
from tests.fixtures.raw_mcp import SECRET, RawMCPServer
from tests.fixtures.timing import LagProbe

pytestmark = pytest.mark.integration

LABEL = {"ar": "أداة اختبار", "en": "Test tool"}
LAG_BOUND_S = 0.100


def _config(
    server_id: str, hashes: dict[str, str], tools, *, timeout_s=3.0, max_concurrency=8, max_bytes=262144
):
    entries = [
        {"name": t, "remote_name": t, "risk": "read", "label": LABEL, "input_schema_sha256": hashes[t]}
        for t in tools
    ]
    return MCPConfig.model_validate(
        {
            "servers": [
                {
                    "id": server_id,
                    "url_env": "URL",
                    "internal": True,
                    "profiles": ["default"],
                    "timeout_s": timeout_s,
                    "max_result_bytes": max_bytes,
                    "max_concurrency": max_concurrency,
                    "tools": entries,
                }
            ]
        }  # fmt: skip
    )


async def _client(config: MCPConfig, url: str) -> MCPClient:
    client = MCPClient(
        resolve(config, profile="default", allow_external=False, environ={"URL": url}),
        hash_key="test-content-hash-key",
        client_version="test",
    )
    client.start()
    assert await client.wait_connected(10), client.health()
    return client


def _broker(client: MCPClient, tools: list[str], guard=None) -> ToolBroker:
    registry = ToolRegistry()
    register_mcp_tools(registry, client)
    return ToolBroker(registry, guard or GuardEngine(), {"cap": tools}, {"cap": []})


async def _call(broker, name, *, user="u-res", audit=None, args=None):
    return await broker.execute(
        name,
        args or {},
        caller="cap",
        principal=Principal(user_id=user, permissions=("cap:cap",)),
        conversation_id="c-res",
        on_audit=audit.append if audit is not None else None,
    )


async def _general_turn_s(services) -> float:
    principal = Principal(user_id=f"u-g7-{uuid.uuid4().hex[:6]}", permissions=("cap:general",))
    request = TurnRequest(
        conversation_id=str(uuid.uuid4()), principal=principal, message="ما عاصمة المملكة؟", locale="ar"
    )
    started = time.monotonic()
    events = [e async for e in TurnRunner(services).stream(request, "req-g7")]
    assert events[-1].type == "done"
    return time.monotonic() - started


@pytest.fixture(scope="module")
def raw():
    server = RawMCPServer()
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="module")
async def raw_hashes(raw):
    return await tool_hashes(raw.url)


# ------------------------------------------------------------------ G7: the event loop (item 8)


@pytest.mark.parametrize("tool, code", [("flood", "tool_result_too_large"), ("trickle", "tool_timeout")])
async def test_a_flooding_or_trickling_server_never_stalls_the_loop_or_other_turns(
    raw, raw_hashes, services, litellm, tool, code
):
    """A server streaming 1 MiB of notifications before its result, or a byte every 2 s: the loop
    lags under 100 ms, another user's call on the same server and a general turn take their
    normal time, and the trickling connection is closed when the call ends."""
    baseline = min([await _general_turn_s(services) for _ in range(2)])
    client = await _client(_config("raw", raw_hashes, ["quick", tool], timeout_s=4.0), raw.url)
    try:
        broker = _broker(client, ["raw.quick", f"raw.{tool}"])
        quick_alone = time.monotonic()
        assert (await _call(broker, "raw.quick")).status == "ok"
        quick_alone = time.monotonic() - quick_alone
        with LagProbe() as probe:
            started = time.monotonic()
            slow = asyncio.create_task(_call(broker, f"raw.{tool}", user="u-slow"))
            await asyncio.sleep(0.3)
            other = time.monotonic()
            assert (await _call(broker, "raw.quick", user="u-other")).status == "ok"
            other = time.monotonic() - other
            during = await _general_turn_s(services)
            result = await slow
            elapsed = time.monotonic() - started
        assert (result.status, result.error) == ("error", code)
        assert probe.worst < LAG_BOUND_S, probe.worst
        assert other < quick_alone + 0.5, (other, quick_alone)
        assert during < baseline + 1.0, (during, baseline)
        assert elapsed < 4.0 + 1.5  # the wall-clock deadline, whatever the server keeps sending
        for _ in range(30):  # the server sees the connection closed within one byte's interval
            if raw.open_streams == 0:
                break
            await asyncio.sleep(0.1)
        assert raw.open_streams == 0
        print(f"G7 {tool}: lag max {probe.worst * 1000:.1f} ms, other call {other * 1000:.0f} ms "
              f"(alone {quick_alone * 1000:.0f} ms), general {during:.2f} s (normal {baseline:.2f} s), "
              f"call ended in {elapsed:.2f} s")  # fmt: skip
    finally:
        await client.stop()


# ------------------------------------------------------------------ the breaker (item 9)


@pytest.fixture(scope="module")
async def fixture_server():
    server = FixtureServer()
    await server.start()
    yield server
    await server.stop()


@pytest.fixture(scope="module")
async def fx_hashes(fixture_server):
    return await tool_hashes(fixture_server.url)


def _opened(connection, now: list[float]) -> None:
    connection.breaker = Breaker(clock=lambda: now[0])
    for _ in range(5):
        connection.breaker.failure()
    assert connection.breaker.state == "open"
    now[0] += 30  # half open: the next call is the one trial


async def test_a_failed_or_refused_trial_reopens_and_a_successful_one_closes(fixture_server, fx_hashes):
    """A cancelled trial is released, not reopened: tests/integration/test_mcp_sessions.py (ADR-0244)."""
    client = await _client(
        _config("fx", fx_hashes, ["echo", "slow", "big"], timeout_s=1.0, max_bytes=1024), fixture_server.url
    )
    connection = client.servers["fx"]
    echo, slow, big = (connection.entry.tool(n) for n in ("echo", "slow", "big"))
    now = [1000.0]
    try:
        trials = {
            "failed": (slow, {"seconds": 5}),  # past the 1 s deadline: tool_timeout
            "refused": (big, {"size": 4096}),  # an answer past the cap: a ToolFailure
        }
        for kind, call in trials.items():
            _opened(connection, now)
            with pytest.raises(ToolFailure):
                await connection.call(*call)
            assert connection.breaker.state == "open" and not connection.breaker.trial_in_progress, kind
            with pytest.raises(ToolFailure) as refused:  # open: nothing is sent
                await connection.call(echo, {"text": "x"})
            assert refused.value.code == "tool_unavailable"
            now[0] += 30
            assert (await connection.call(echo, {"text": kind})).data == {"echo": kind}
            assert connection.breaker.state == "closed" and connection.breaker.failures == 0, kind
    finally:
        await client.stop()


async def test_fifty_waits_for_a_full_queue_are_busy_and_leave_the_breaker_closed(fixture_server, fx_hashes):
    client = await _client(
        _config("fx", fx_hashes, ["echo"], timeout_s=0.3, max_concurrency=1), fixture_server.url
    )
    connection = client.servers["fx"]
    echo = connection.entry.tool("echo")
    try:
        await connection._semaphore.acquire()  # the queue is full
        outcomes = await asyncio.gather(
            *(connection.call(echo, {"text": str(i)}) for i in range(50)), return_exceptions=True
        )
        assert {type(o).__name__ for o in outcomes} == {"ToolFailure"}
        assert {o.code for o in outcomes} == {"tool_busy"}
        assert connection.breaker.state == "closed" and connection.breaker.failures == 0
        connection._semaphore.release()
        assert (await connection.call(echo, {"text": "after"})).data == {"echo": "after"}
    finally:
        await client.stop()


async def test_the_first_call_after_a_server_restart_succeeds_after_one_reinitialisation(fx_hashes):
    """I9, asserted directly: no retry in the test."""
    server = FixtureServer()
    await server.start()
    client = await _client(_config("fx", fx_hashes, ["echo"]), server.url)
    connection = client.servers["fx"]
    echo = connection.entry.tool("echo")
    try:
        assert (await connection.call(echo, {"text": "1"})).data == {"echo": "1"}
        sessions = connection.sessions
        await server.stop()
        await server.start()  # same port; it knows no session: 404 on the next request
        assert (await connection.call(echo, {"text": "2"})).data == {"echo": "2"}
        assert connection.sessions == sessions + 1
    finally:
        await client.stop()
        await server.stop()


# ------------------------------------------------------------------ server-initiated requests (item 14)


async def test_sampling_elicitation_and_roots_requests_are_dropped_and_the_call_goes_on(
    fixture_server, fx_hashes, caplog
):
    """None of the three is served: each is dropped unanswered and logged by its code only
    (ADR-0246), and the call ends with the server's answer within its own deadline."""
    caplog.set_level(logging.WARNING, logger="app.mcp.transport")
    client = await _client(_config("fx", fx_hashes, ["asks"], timeout_s=10.0), fixture_server.url)
    try:
        result = await _call(_broker(client, ["fx.asks"]), "fx.asks")
        assert result.status == "ok", result.error
        assert result.data == {
            "refused": [],
            "unanswered": ["sampling", "elicitation", "roots"],
            "answer": "done",
        }
        dropped = [r.getMessage() for r in caplog.records if r.name == "app.mcp.transport"]
        assert dropped == ["server_request_dropped"] * 3
    finally:
        await client.stop()


# ------------------------------------------------------------------ G8 (item 12)


def _text_of(spans, audit, caplog) -> str:
    attributes = [
        str(v)
        for s in spans.get_finished_spans()
        for v in [*(s.attributes or {}).values(), *(a for e in s.events for a in e.attributes.values())]
    ]
    # The fixture servers run in this process; their own loggers (the server side, which the
    # platform never runs) are not the agent's logs.
    server_side = ("sse_starlette", "uvicorn", "mcp.server", "tests.")
    logs = [
        r.getMessage() + str(r.exc_info or "") + str(r.exc_text or "")
        for r in caplog.records
        if not r.name.startswith(server_side)
    ]
    return "\n".join([*attributes, json.dumps(audit, ensure_ascii=False), *logs])


async def test_an_exception_and_a_garbled_answer_leave_no_server_text_anywhere(
    raw, raw_hashes, fixture_server, fx_hashes, spans, caplog
):
    quiet_libraries()
    caplog.set_level(logging.DEBUG)
    spans.clear()
    audit: list[dict] = []
    fx = await _client(_config("fx", fx_hashes, ["fails"]), fixture_server.url)
    garbled = await _client(_config("raw", raw_hashes, ["garbled"], timeout_s=2.0), raw.url)
    try:
        failed = await _call(_broker(fx, ["fx.fails"]), "fx.fails", audit=audit)
        broken = await _call(_broker(garbled, ["raw.garbled"]), "raw.garbled", audit=audit)
    finally:
        await fx.stop()
        await garbled.stop()
    assert (failed.status, failed.error) == ("error", "tool_error")
    assert broken.status == "error" and broken.error in {"tool_timeout", "tool_unavailable"}
    text = _text_of(spans, audit, caplog)
    assert "private detail" not in text and SECRET not in text
    assert [entry["error"] for entry in audit] == [failed.error, broken.error]


async def test_a_handler_exception_is_a_code_never_its_text(services):
    from pydantic import BaseModel

    from app.tools.spec import ToolSpec

    class NoArgs(BaseModel):
        pass

    async def explode(args, ctx) -> ToolOutput:
        raise RuntimeError("a document's text the error quotes: 1012345672")

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="explode", description_ar="x", description_en="x", input_schema=NoArgs, risk="read",
            required_permission=None, timeout_s=2, max_retries=0, handler=explode,
        ),
        "cap",
    )  # fmt: skip
    audit: list[dict] = []
    result = await _call(ToolBroker(registry, GuardEngine(), {"cap": ["explode"]}), "explode", audit=audit)
    assert (result.status, result.error) == ("error", "tool_failed") and audit[0]["error"] == "tool_failed"


async def test_a_national_id_in_a_structured_key_is_redacted_except_for_the_role_exception(services, role):
    """structuredContent keys are checked at POST_TOOL like values (ADR-0234): a user sees the key
    redacted; the holder of the role exception (a role-scoped allow over a global redaction,
    ADR-0107) sees it."""
    name, add = role
    tenant = "default"
    group = f"g-{uuid.uuid4().hex[:8]}"
    redaction = {"entities": ["SA_NATIONAL_ID"], "score_threshold": 0.5}
    await add(
        scope="global",  # only global contains a role scope (ADR-0108); switched off after the test
        checkpoints=[Checkpoint.POST_TOOL],
        checker="presidio",
        config=redaction,
        action="redact",
        group=group,
    )
    await add(
        checkpoints=[Checkpoint.POST_TOOL], checker="presidio", config=redaction, action="allow", group=group
    )

    from pydantic import BaseModel

    from app.tools.spec import ToolSpec

    class NoArgs(BaseModel):
        pass

    async def keyed(args, ctx) -> ToolOutput:
        data = {"holders": {"1012345672": "صاحب الهوية"}, "count": 1}
        return ToolOutput(content=json.dumps(data, ensure_ascii=False), data=data)

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="keyed", description_ar="x", description_en="x", input_schema=NoArgs, risk="read",
            required_permission=None, timeout_s=5, max_retries=0, handler=keyed,
        ),
        "cap",
    )  # fmt: skip
    broker = ToolBroker(registry, services.guard, {"cap": ["keyed"]})

    async def run(*policy_roles: str):
        principal = Principal(
            user_id=f"u-key-{uuid.uuid4().hex[:6]}",
            tenant_id=tenant,
            permissions=("cap:cap",),
            policy_roles=policy_roles,
        )
        return await broker.execute("keyed", {}, caller="cap", principal=principal, conversation_id="c-key")

    user = await run()
    assert (
        user.status == "ok" and "1012345672" not in json.dumps(user.data) and "1012345672" not in user.content
    )
    assert user.data["count"] == 1 and list(user.data["holders"].values()) in ([], ["صاحب الهوية"])
    holder = await run(f"policy:{name}")
    assert holder.status == "ok" and holder.data["holders"] == {"1012345672": "صاحب الهوية"}
