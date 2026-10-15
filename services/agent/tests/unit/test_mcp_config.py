"""config/mcp.yaml validation (phase 05, G2; ADR-0198 to ADR-0204, ADR-0217): Streamable HTTP
only, read tools only, no delete tool, closed injection sources, unique names; each server
enabled per profile and flag; the capped transport's refusals (I2, I7)."""

import json

import httpx
import pytest

from app.mcp.config import MCPConfig, MCPConfigError, load_mcp_config, resolve
from app.mcp.transport import TOO_LARGE, CappedTransport

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
    transport = CappedTransport(100, _Upstream(b"{" + b"x" * 10_000))
    response = await transport.handle_async_request(_post(7))
    await response.aread()
    assert response.json() == {"jsonrpc": "2.0", "id": 7, "error": {"code": -32099, "message": TOO_LARGE}}
    assert transport.sizes["c1"].too_large and transport.sizes["c1"].response_bytes <= 100 + 10_001


async def test_the_server_stream_is_never_opened_and_encoding_must_be_identity():
    upstream = _Upstream(b"{}")
    transport = CappedTransport(100, upstream)
    response = await transport.handle_async_request(httpx.Request("GET", "http://s/mcp"))
    assert response.status_code == 405 and upstream.requests == []
    encoded = CappedTransport(100, _Upstream(b"{}", encoding="gzip"))
    response = await encoded.handle_async_request(_post(3))
    await response.aread()
    assert response.json()["error"]["message"] == "content_encoding_refused"


async def test_an_sse_answer_is_read_up_to_its_event():
    events = b'event: message\ndata: {"jsonrpc":"2.0","id":9,"result":{}}\n\n' + b"data: " + b"y" * 5000
    transport = CappedTransport(4096, _Upstream(events, content_type="text/event-stream"))
    response = await transport.handle_async_request(_post(9))
    body = await response.aread()
    assert b'"id":9' in body and not transport.sizes["c1"].too_large
