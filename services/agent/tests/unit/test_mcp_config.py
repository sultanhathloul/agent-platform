"""config/mcp.yaml validation (phase 05, G2; ADR-0198 to ADR-0204, ADR-0217): Streamable HTTP
only, read tools only, no delete tool, closed injection sources, unique names; each server
enabled per profile and flag; the capped transport's refusals (I2, I7)."""

import asyncio
import json
import time

import httpx
import pytest

from app.mcp.config import MCPConfig, MCPConfigError, load_mcp_config, resolve
from app.mcp.transport import TOO_LARGE, TRANSPORT_TIMEOUT, CappedTransport

HASH = "a" * 64


def _server(**overrides) -> dict:
    tool = {
        "name": "geocode",
        "remote_name": "geocode",
        "risk": "read",
        "label": {"ar": "بحث", "en": "Search"},
        "input_schema_sha256": HASH,
    }
    tool.update(overrides.pop("tool", {}))
    return {
        "id": "routing",
        "url_env": "MCP_ROUTING_URL",
        "internal": True,
        "profiles": ["default"],
        "tools": [tool],
        **overrides,
    }


def _invalid(server: dict) -> str:
    with pytest.raises(ValueError) as exc:
        MCPConfig.model_validate({"servers": [server]})
    return str(exc.value)


def test_a_valid_server_and_its_defaults():
    [server] = MCPConfig.model_validate({"servers": [_server()]}).servers
    assert (server.timeout_s, server.max_result_bytes, server.max_concurrency) == (10.0, 262144, 8)
    assert server.transport == "streamable_http" and server.auth == "none"


def test_stdio_is_refused():
    assert "Streamable HTTP" in _invalid(_server(transport="stdio"))


def test_a_write_tool_is_refused_at_startup(tmp_path):
    assert "write-class" in _invalid(_server(tool={"risk": "write"}))
    path = tmp_path / "mcp.yaml"
    path.write_text(json.dumps({"servers": [_server(tool={"risk": "write"})]}))
    with pytest.raises(MCPConfigError):
        load_mcp_config(path)


def test_a_transport_operation_is_its_own_class_and_may_write_on_its_server_only():
    """ADR-0230 (supersedes ADR-0200): D6 stands for model-callable tools; a transport operation
    is never a broker tool, may be marked write (it writes on its own server), and never deletes."""
    [server] = MCPConfig.model_validate(
        {"servers": [_server(tool={"class": "transport", "risk": "write"})]}
    ).servers
    assert server.tools[0].class_ == "transport" and server.tools_of("tool") == []
    assert "write-class" in _invalid(_server(tool={"class": "tool", "risk": "write"}))
    assert "delete" in _invalid(
        _server(tool={"class": "transport", "name": "purge_all", "remote_name": "purge_all"})
    )
    assert "class" in _invalid(_server(tool={"class": "both"}))


def test_the_shipped_rag_operations_are_transport_and_never_broker_tools(config_dir):
    from app.mcp.client import MCPClient
    from app.mcp.tools import mcp_tool_specs, transport_operations

    config = load_mcp_config(config_dir / "mcp.yaml")
    rag = next(s for s in config.servers if s.id == "rag")
    assert {t.name: (t.class_, t.risk) for t in rag.tools} == {
        "register": ("transport", "write"),
        "status": ("transport", "read"),
        "search": ("transport", "read"),
        "fetch": ("transport", "read"),
    }
    client = MCPClient(
        resolve(config, profile="default", allow_external=False, environ={}), hash_key="k", client_version="t"
    )
    names = {spec.name for spec in mcp_tool_specs(client)}
    assert names == {"routing.geocode", "routing.route"}
    assert transport_operations(client) == {"rag.register", "rag.status", "rag.search", "rag.fetch"}


@pytest.mark.parametrize("name", ["delete_file", "remove", "drop_index", "purge_cache"])
def test_no_delete_tool_is_ever_allowed(name):
    assert "deletes" in _invalid(_server(tool={"name": name, "remote_name": name}))
    assert "deletes" in _invalid(_server(tool={"remote_name": f"x_{name}"}))


def test_injected_sources_are_a_closed_list():
    assert _invalid(_server(tool={"injected": {"tenant": "principal.permissions"}}))
    ok = MCPConfig.model_validate(
        {"servers": [_server(tool={"injected": {"t": "principal.tenant_id", "c": "scope.collections"}})]}
    )
    assert ok.servers[0].tools[0].injected == {"t": "principal.tenant_id", "c": "scope.collections"}


def test_unknown_fields_secrets_and_urls_are_not_part_of_the_file():
    assert _invalid({**_server(), "url": "http://x"})
    assert _invalid({**_server(), "token": "secret"})


def test_names_are_unique():
    with pytest.raises(ValueError):
        MCPConfig.model_validate({"servers": [_server(), _server()]})
    server = _server()
    server["tools"] = server["tools"] * 2
    assert _invalid(server)


def test_resolution_per_profile_flag_url_and_token():
    config = MCPConfig.model_validate(
        {
            "servers": [
                _server(),
                _server(id="other", profiles=["airgap"]),
                _server(id="ext", internal=False, profiles=["default", "airgap"]),
                _server(id="secured", auth={"bearer_env": "SECURED_TOKEN"}),
            ]
        }
    )
    env = {"MCP_ROUTING_URL": "http://r/mcp"}
    reasons = {
        r.entry.id: r.reason for r in resolve(config, profile="default", allow_external=False, environ=env)
    }
    assert reasons == {
        "routing": "",
        "other": "profile",
        "ext": "external_not_allowed",
        "secured": "no_token",
    }
    airgap = {
        r.entry.id: r.reason for r in resolve(config, profile="airgap", allow_external=True, environ=env)
    }
    assert airgap["ext"] == "external_in_airgap" and airgap["routing"] == "profile"
    assert resolve(config, profile="default", allow_external=False, environ={})[0].reason == "no_url"
    secured = resolve(config, profile="default", allow_external=False, environ={**env, "SECURED_TOKEN": "t"})[
        3
    ]
    assert secured.enabled and secured.token == "t"


# ------------------------------------------------------------------ transport (I2, I7)


class _Upstream(httpx.AsyncBaseTransport):
    def __init__(self, body: bytes, content_type: str = "application/json", encoding: str | None = None):
        self.body, self.content_type, self.encoding, self.requests = body, content_type, encoding, []

    async def handle_async_request(self, request):
        self.requests.append(request)
        headers = {"content-type": self.content_type}
        if self.encoding:
            headers["content-encoding"] = self.encoding
        return httpx.Response(200, headers=headers, stream=httpx.ByteStream(self.body))


def _post(rid: int = 7) -> httpx.Request:
    body = {
        "jsonrpc": "2.0",
        "id": rid,
        "method": "tools/call",
        "params": {"name": "x", "_meta": {"platform_call": "c1"}},
    }
    return httpx.Request("POST", "http://s/mcp", json=body)


async def test_a_body_over_the_cap_becomes_a_json_rpc_error_for_that_request():
    transport = CappedTransport(100, inner=_Upstream(b"{" + b"x" * 10_000))
    response = await transport.handle_async_request(_post(7))
    await response.aread()
    assert response.json() == {"jsonrpc": "2.0", "id": 7, "error": {"code": -32099, "message": TOO_LARGE}}
    assert transport.sizes["c1"].too_large and transport.sizes["c1"].response_bytes <= 100 + 10_001


async def test_the_server_stream_is_never_opened_and_encoding_must_be_identity():
    upstream = _Upstream(b"{}")
    transport = CappedTransport(100, inner=upstream)
    response = await transport.handle_async_request(httpx.Request("GET", "http://s/mcp"))
    assert response.status_code == 405 and upstream.requests == []
    encoded = CappedTransport(100, inner=_Upstream(b"{}", encoding="gzip"))
    response = await encoded.handle_async_request(_post(3))
    await response.aread()
    assert response.json()["error"]["message"] == "content_encoding_refused"


async def test_an_sse_answer_is_read_up_to_its_event():
    events = b'event: message\ndata: {"jsonrpc":"2.0","id":9,"result":{}}\n\n' + b"data: " + b"y" * 5000
    transport = CappedTransport(4096, inner=_Upstream(events, content_type="text/event-stream"))
    response = await transport.handle_async_request(_post(9))
    body = await response.aread()
    assert b'"id":9' in body and not transport.sizes["c1"].too_large


class _Chunks(httpx.AsyncByteStream):
    """A body sent in the given chunks, `pause` seconds apart."""

    def __init__(self, chunks, pause: float = 0.0):
        self.chunks, self.pause, self.closed = list(chunks), pause, False

    async def __aiter__(self):
        for chunk in self.chunks:
            if self.pause:
                await asyncio.sleep(self.pause)
            yield chunk

    async def aclose(self):
        self.closed = True


class _Streaming(httpx.AsyncBaseTransport):
    def __init__(self, stream: _Chunks):
        self.stream = stream

    async def handle_async_request(self, request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=self.stream)


def _event(message: dict) -> bytes:
    return b"event: message\ndata: " + json.dumps(message).encode() + b"\n\n"


async def test_sse_events_reach_the_sdk_whole_even_when_split_across_chunks():
    """The incremental parser (ADR-0236): a server's request on the stream is passed on, an event
    split anywhere is passed whole, and the stream ends after the answer."""
    sampling = _event({"jsonrpc": "2.0", "id": "s1", "method": "sampling/createMessage", "params": {}})
    answer = _event({"jsonrpc": "2.0", "id": 9, "result": {"ok": True}})
    after = _event({"jsonrpc": "2.0", "method": "notifications/progress", "params": {}})
    body = sampling + answer + after
    chunks = [body[i : i + 7] for i in range(0, len(body), 7)]
    transport = CappedTransport(4096, inner=_Streaming(_Chunks(chunks)))
    response = await transport.handle_async_request(_post(9))
    received = await response.aread()
    assert received == sampling + answer and transport.sizes["c1"].response_bytes == len(sampling + answer)


async def test_every_byte_counts_toward_the_cap_notifications_included():
    progress = _event({"jsonrpc": "2.0", "method": "notifications/progress", "params": {"progress": 1}})
    stream = _Chunks([progress] * 100)  # ~9 KB of notifications and never an answer
    transport = CappedTransport(2048, inner=_Streaming(stream))
    response = await transport.handle_async_request(_post(4))
    received = await response.aread()
    error = json.loads(received.rsplit(b"data: ", 1)[1])
    assert error == {"jsonrpc": "2.0", "id": 4, "error": {"code": -32099, "message": TOO_LARGE}}
    assert transport.sizes["c1"].too_large and stream.closed and len(received) < 2048 + 200


async def test_a_trickling_answer_ends_at_the_wall_clock_deadline_and_closes_the_connection():
    stream = _Chunks([b":"] * 1000, pause=0.2)  # a byte every 0.2 s: no read ever times out
    transport = CappedTransport(262144, timeout_s=1.0, inner=_Streaming(stream))
    started = time.monotonic()
    response = await transport.handle_async_request(_post(5))
    received = await response.aread()
    assert time.monotonic() - started < 1.6
    assert json.loads(received.rsplit(b"data: ", 1)[1])["error"]["message"] == TRANSPORT_TIMEOUT
    assert stream.closed


async def test_a_stream_that_ends_without_its_answer_is_an_error_for_that_request():
    transport = CappedTransport(4096, inner=_Streaming(_Chunks([b": keep-alive\n\n"])))
    response = await transport.handle_async_request(_post(6))
    received = await response.aread()
    assert json.loads(received.rsplit(b"data: ", 1)[1])["error"]["message"] == "tool_unavailable"
