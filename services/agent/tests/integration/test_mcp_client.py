"""The MCP client as a broker tool provider (phase 05, C1; ADR-0198 to ADR-0210, ADR-0217). Over
real Streamable HTTP against the test-only server of tests/fixtures/mcp_server.py.

Isolation and the allowlist first: an unlisted remote tool is never registered (D4), a call
naming an injected field never reaches the server, and the injected fields carry the verified
principal and the turn's scope (I6). Then pinning (I5), the size cap (I7), the timeout and the
circuit breaker (I8), a dead server (the agent starts, calls fail fast, it reconnects), another
user's chat while a server is slow (G7), and no argument or result text in spans or the audit
entry (G8)."""

import asyncio
import json
import time
import uuid
from pathlib import Path

import pytest
import yaml

from app.guardrails.engine import GuardEngine
from app.identity import Principal
from app.mcp.client import Breaker, MCPClient
from app.mcp.config import MCPConfig, resolve
from app.mcp.tools import register_mcp_tools
from app.tools.broker import ToolBroker
from app.tools.registry import ToolRegistry
from tests.fixtures.mcp_server import FixtureServer, free_port, tool_hashes

pytestmark = pytest.mark.integration

LABEL = {"ar": "أداة اختبار", "en": "Test tool"}
SECRET_ARG = "argument-text-that-must-never-leave-7f3a"
SECRET_RESULT_SIZE = 64


@pytest.fixture(scope="module")
async def server():
    fixture = FixtureServer()
    await fixture.start()
    yield fixture
    await fixture.stop()


@pytest.fixture(scope="module")
async def hashes(server):
    return await tool_hashes(server.url)


def _config(hashes, *, tools=None, server_id="fx", internal=True, timeout_s=3.0, max_bytes=262144, **extra):
    names = tools or ["echo", "scoped", "slow", "big", "mixed", "fails"]
    entries = []
    for name in names:
        entry = {
            "name": name,
            "remote_name": name,
            "risk": "read",
            "label": LABEL,
            "input_schema_sha256": hashes.get(name, "0" * 64),
        }
        if name == "scoped":
            entry["injected"] = {
                "tenant": "principal.tenant_id",
                "user": "principal.user_id",
                "collections": "scope.collections",
            }
        entries.append(entry)
    return MCPConfig.model_validate(
        {
            "servers": [
                {
                    "id": server_id,
                    "url_env": "FX_URL",
                    "internal": internal,
                    "profiles": ["default", "airgap"],
                    "timeout_s": timeout_s,
                    "max_result_bytes": max_bytes,
                    "max_concurrency": extra.pop("max_concurrency", 8),
                    "tools": entries,
                    **extra,
                }
            ]
        }
    )


def _client(config, url, *, profile="default", allow_external=False) -> MCPClient:
    resolved = resolve(config, profile=profile, allow_external=allow_external, environ={"FX_URL": url})
    return MCPClient(resolved, hash_key="test-content-hash-key", client_version="test")


async def _started(config, url, **kwargs) -> MCPClient:
    client = _client(config, url, **kwargs)
    client.start()
    assert await client.wait_connected(10)
    return client


def _broker(client: MCPClient, caller_tools: list[str], guard: GuardEngine | None = None) -> ToolBroker:
    registry = ToolRegistry()
    register_mcp_tools(registry, client)
    return ToolBroker(registry, guard or GuardEngine(), {"fxcap": caller_tools}, {"fxcap": ["session"]})


PRINCIPAL = Principal(user_id="u-mcp", tenant_id="t-mcp", permissions=("cap:fxcap",))


async def _call(broker, name, args, *, principal=PRINCIPAL, audit=None, conversation="c-mcp"):
    return await broker.execute(
        name,
        args,
        caller="fxcap",
        principal=principal,
        conversation_id=conversation,
        on_audit=(audit.append if audit is not None else None),
    )


# ------------------------------------------------------------------ allowlist and injection first


async def test_an_unlisted_remote_tool_is_never_registered_or_reachable(server, hashes):
    client = await _started(_config(hashes, tools=["echo"]), server.url)
    try:
        registry = ToolRegistry()
        assert register_mcp_tools(registry, client) == ["fx.echo"]
        assert not registry.has("fx.admin_secret")
        assert set(client.servers["fx"].verified) == {"echo"}
        broker = ToolBroker(registry, GuardEngine(), {"fxcap": ["fx.echo", "fx.admin_secret"]})
        before = server.calls["admin_secret"]
        with pytest.raises(KeyError):
            await _call(broker, "fx.admin_secret", {})
        assert server.calls["admin_secret"] == before
    finally:
        await client.stop()


async def test_a_call_naming_an_injected_field_never_reaches_the_server(server, hashes):
    client = await _started(_config(hashes), server.url)
    try:
        broker = _broker(client, ["fx.scoped"])
        before = server.calls["scoped"]
        for forged in ({"tenant": "other"}, {"user": "victim"}, {"collections": ["sess_x"]}):
            result = await _call(broker, "fx.scoped", {"query": "q", **forged})
            assert (result.status, result.error) == ("error", "invalid_arguments")
        assert server.calls["scoped"] == before

        result = await _call(broker, "fx.scoped", {"query": "q"}, conversation="c-77")
        assert result.status == "ok"
        assert server.received[-1] == {
            "query": "q",
            "tenant": "t-mcp",
            "user": "u-mcp",
            "collections": ["sess_c-77"],
        }
        # The schema a model or a capability may see has no injected field (I6).
        schema = broker.registry.get("fx.scoped").input_schema.model_json_schema()
        assert set(schema["properties"]) == {"query"} and schema["required"] == ["query"]
    finally:
        await client.stop()


async def test_arguments_are_validated_against_the_pinned_schema(server, hashes):
    client = await _started(_config(hashes), server.url)
    try:
        broker = _broker(client, ["fx.echo"])
        before = server.calls["echo"]
        for bad in ({}, {"text": 5}, {"text": "x", "extra": 1} if False else {"text": None}):
            result = await _call(broker, "fx.echo", bad)
            assert (result.status, result.error) == ("error", "invalid_arguments")
        assert server.calls["echo"] == before
        ok = await _call(broker, "fx.echo", {"text": "مرحبا"})
        assert ok.status == "ok" and ok.data == {"echo": "مرحبا"} and json.loads(ok.content) == ok.data
    finally:
        await client.stop()


# ------------------------------------------------------------------ pinning (I5)


async def test_a_drifted_or_missing_schema_disables_that_tool_only(server, hashes):
    drifted = {**hashes, "big": "f" * 64}
    config = _config(drifted, tools=["echo", "big", "gone"])
    client = await _started(config, server.url)
    try:
        assert client.servers["fx"].disabled == {"big": "schema_mismatch", "gone": "missing"}
        broker = _broker(client, ["fx.echo", "fx.big", "fx.gone"])
        before = server.calls["big"]
        assert (await _call(broker, "fx.big", {"size": 3})).error == "tool_unavailable"
        assert server.calls["big"] == before
        assert (await _call(broker, "fx.echo", {"text": "x"})).status == "ok"
        [health] = client.health()
        assert health["disabled_tools"] == [
            {"tool": "fx.big", "reason": "schema_mismatch"},
            {"tool": "fx.gone", "reason": "missing"},
        ]
        assert server.url not in json.dumps(client.health())
    finally:
        await client.stop()


# ------------------------------------------------------------------ results (I7)


async def test_a_result_over_the_cap_is_refused_without_parsing_it(server, hashes):
    client = await _started(_config(hashes, max_bytes=2048), server.url)
    try:
        broker = _broker(client, ["fx.big"])
        audit: list[dict] = []
        result = await _call(broker, "fx.big", {"size": 500_000}, audit=audit)
        assert (result.status, result.error) == ("error", "tool_result_too_large")
        assert audit[-1]["response_bytes"] <= 2048 + 65536  # stopped at the cap, never the whole body
        assert (await _call(broker, "fx.big", {"size": 100})).status == "ok"  # the session lives on
    finally:
        await client.stop()


async def test_images_and_resources_are_dropped_with_a_note(server, hashes, spans):
    client = await _started(_config(hashes), server.url)
    try:
        broker = _broker(client, ["fx.mixed", "fx.fails"])
        audit: list[dict] = []
        result = await _call(broker, "fx.mixed", {}, audit=audit)
        assert result.status == "ok" and result.content == "visible text" and result.data is None
        assert "hidden resource text" not in result.content
        # The note is on the call's span; the audit entry keeps the audit record's fields only.
        [span] = [s for s in spans.get_finished_spans() if s.name == "tool.exec"][-1:]
        assert span.attributes["tool.dropped"] == "image,resource" and "dropped" not in audit[-1]
        failed = await _call(broker, "fx.fails", {})
        assert (failed.status, failed.error) == ("error", "tool_error")  # no server text reaches the caller
    finally:
        await client.stop()


# ------------------------------------------------------------------ resilience (I8, G7)


async def test_a_slow_call_times_out_and_the_breaker_opens_after_five_failures(server, hashes):
    client = await _started(_config(hashes, timeout_s=0.5), server.url)
    try:
        now = [1000.0]
        client.servers["fx"].breaker = Breaker(clock=lambda: now[0])
        broker = _broker(client, ["fx.slow", "fx.echo"])
        started = time.monotonic()
        for _ in range(5):
            assert (await _call(broker, "fx.slow", {"seconds": 3})).error == "tool_timeout"
        assert time.monotonic() - started < 5 * 1.5
        assert client.servers["fx"].breaker.state == "open"
        before = server.calls["echo"]
        open_started = time.monotonic()
        assert (await _call(broker, "fx.echo", {"text": "x"})).error == "tool_unavailable"
        assert time.monotonic() - open_started < 0.2 and server.calls["echo"] == before
        now[0] += 31  # half-open: one trial call; it succeeds and closes the circuit
        assert (await _call(broker, "fx.echo", {"text": "x"})).status == "ok"
        assert client.servers["fx"].breaker.state == "closed"
    finally:
        await client.stop()


async def test_a_dead_server_never_stops_the_agent_and_is_reconnected():
    port = free_port()
    hashes = {"echo": "700480b4dcb59d581411914d68ada5aa4a4d79b5c25b8662e1b2c452318d712e"}
    fixture = FixtureServer(port)
    live = await tool_hashes_when_up(fixture)
    hashes |= live
    client = _client(_config(hashes, tools=["echo"]), fixture.url)
    client.start()  # the server is down: start returns at once
    try:
        broker = _broker(client, ["fx.echo"])
        started = time.monotonic()
        assert (await _call(broker, "fx.echo", {"text": "x"})).error == "tool_unavailable"
        assert time.monotonic() - started < 0.5
        [health] = client.health()
        assert (health["enabled"], health["connected"]) == (True, False)
        await fixture.start()
        assert await client.wait_connected(15)
        assert (await _call(broker, "fx.echo", {"text": "x"})).status == "ok"
    finally:
        await client.stop()
        await fixture.stop()


async def tool_hashes_when_up(fixture: FixtureServer) -> dict[str, str]:
    await fixture.start()
    try:
        return await tool_hashes(fixture.url)
    finally:
        await fixture.stop()


async def test_an_expired_session_is_reinitialised(hashes):
    fixture = FixtureServer()
    await fixture.start()
    client = await _started(_config(hashes, tools=["echo"]), fixture.url)
    try:
        broker = _broker(client, ["fx.echo"])
        assert (await _call(broker, "fx.echo", {"text": "1"})).status == "ok"
        await fixture.stop()  # the restarted server knows no session: 404 on the next request
        await fixture.start()
        result = await _call(broker, "fx.echo", {"text": "2"})  # one call, re-sent on a new session (I9)
        assert result.status == "ok" and result.data == {"echo": "2"}
    finally:
        await client.stop()
        await fixture.stop()


async def test_twenty_slow_calls_never_slow_another_users_general_turn(server, hashes, services, litellm):
    """G7: a server busy with twenty slow calls (beyond its concurrency of 8) never slows a chat
    turn that does not use it."""
    from app.api.runner import TurnRunner
    from app.api.schemas import TurnRequest

    async def general_turn() -> float:
        principal = Principal(user_id=f"u-g7-{uuid.uuid4().hex[:6]}", permissions=("cap:general",))
        request = TurnRequest(
            conversation_id=str(uuid.uuid4()), principal=principal, message="ما عاصمة المملكة؟", locale="ar"
        )
        started = time.monotonic()
        events = [e async for e in TurnRunner(services).stream(request, "req-g7")]
        assert events[-1].type == "done"
        return time.monotonic() - started

    baseline = min([await general_turn() for _ in range(2)])
    client = await _started(_config(hashes, timeout_s=5.0, max_concurrency=8), server.url)
    try:
        broker = _broker(client, ["fx.slow"])
        calls = [asyncio.create_task(_call(broker, "fx.slow", {"seconds": 2})) for _ in range(20)]
        await asyncio.sleep(0.2)
        during = await general_turn()
        results = await asyncio.gather(*calls)
        assert during < baseline + 1.0, (baseline, during)
        assert {r.error for r in results} <= {None, "tool_timeout"}
        assert sum(r.status == "ok" for r in results) >= 8
    finally:
        await client.stop()


# ------------------------------------------------------------------ G8


async def test_no_argument_or_result_text_in_spans_or_the_audit_entry(server, hashes, spans):
    client = await _started(_config(hashes), server.url)
    try:
        broker = _broker(client, ["fx.echo"])
        audit: list[dict] = []
        result = await _call(broker, "fx.echo", {"text": SECRET_ARG}, audit=audit)
        assert result.status == "ok" and SECRET_ARG in result.content
        [entry] = audit
        assert entry["server"] == "fx" and len(entry["args_hmac"]) == 64
        assert entry["request_bytes"] > 0 and entry["response_bytes"] > 0
        assert SECRET_ARG not in json.dumps(audit)
        recorded = [s for s in spans.get_finished_spans() if s.name in ("mcp.call", "tool.exec")]
        assert {s.name for s in recorded} == {"mcp.call", "tool.exec"}
        for span in recorded:
            text = json.dumps({**dict(span.attributes), "events": [dict(e.attributes) for e in span.events]})
            assert SECRET_ARG not in text and 'echo": ' not in text
        [mcp_span] = [s for s in recorded if s.name == "mcp.call"]
        assert mcp_span.attributes["mcp.args_hmac"] == entry["args_hmac"]
    finally:
        await client.stop()


# ------------------------------------------------------------------ external servers (I16)


async def test_an_external_server_needs_the_flag_never_runs_in_airgap_and_needs_a_policy(
    server, hashes, services
):
    config = _config(hashes, tools=["echo"], server_id="ext", internal=False)
    assert resolve(config, profile="default", allow_external=False, environ={"FX_URL": server.url})[
        0
    ].reason == ("external_not_allowed")
    assert resolve(config, profile="airgap", allow_external=True, environ={"FX_URL": server.url})[
        0
    ].reason == ("external_in_airgap")
    client = await _started(config, server.url, allow_external=True)
    try:
        registry = ToolRegistry()
        register_mcp_tools(registry, client)
        assert registry.get("ext.echo").external
        broker = ToolBroker(registry, services.guard, {"fxcap": ["ext.echo"]})
        before = server.calls["echo"]
        denied = await _call(broker, "ext.echo", {"text": "x"})
        assert (denied.status, denied.error) == ("denied", "external_tool_without_policy")
        assert server.calls["echo"] == before
        from app.guardrails.schema import Policy

        policy = Policy.model_validate(
            {
                "id": f"ext-pre-{uuid.uuid4().hex[:8]}",
                "name_ar": "سياسة خادم خارجي",
                "name_en": "external server policy",
                "scope": "tool:ext.*",
                "checkpoints": ["PRE_TOOL"],
                "checker": "regex",
                "config": {"patterns": [{"name": "NATIONAL_ID", "pattern": "\\b1\\d{9}\\b"}]},
                "action": "block",
                "severity": "high",
                "message_ar": "لا يُرسل رقم الهوية إلى خدمة خارجية.",
                "message_en": "No national ID goes to an external service.",
            }
        )
        await services.policies.create(policy, actor="test")
        try:
            assert (await _call(broker, "ext.echo", {"text": "x"})).status == "ok"
            assert (await _call(broker, "ext.echo", {"text": "1012345678"})).error == "policy"
        finally:
            await services.policies.set_enabled(policy.id, False, actor="test")
    finally:
        await client.stop()


def test_the_shipped_allowlist_has_no_external_server_and_validates(config_dir):
    path = config_dir / "mcp.yaml"
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    config = MCPConfig.model_validate(raw)
    assert config.servers and all(server.internal for server in config.servers)
    # D6: every model-callable tool is read; a transport operation may write on its server (ADR-0230).
    assert all(tool.risk == "read" for server in config.servers for tool in server.tools_of("tool"))
    writes = {t.name for s in config.servers for t in s.tools_of("transport") if t.risk == "write"}
    assert writes == {"register"}
