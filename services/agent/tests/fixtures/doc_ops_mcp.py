"""A document backend's MCP server that misbehaves on demand (the PR #12 review, decisions 4 and 10;
ADR-0265): the reference RAG server's four transport operations (`register`, `status`, `search`,
`fetch`) written byte by byte, as tests/fixtures/raw_mcp.py is, so registration and the evidence
fetch can be tried against a server that hangs, trickles or floods. It runs uvicorn in a thread of
its own, with its own event loop, so its work never shows in the event-loop lag a test measures.

`mode[operation]` decides each operation's answer:

- `ok`: at once — `register` {"registered": true}, `status` indexed, `search` no hits, `fetch` a
  text for every reference asked;
- `hung`: nothing, until the client goes away;
- `trickle`: an SSE stream that sends one byte every TRICKLE_S and never the result;
- `flood`: FLOOD_BYTES of `notifications/progress` events, then the result (past any byte cap);
- `fail`: a JSON-RPC internal error at once;
- a number: `ok` after that many seconds, unless the client goes away first.

`open_requests[operation]` counts the calls open now, `peak[operation]` at worst since `reset`."""

import asyncio
import json
from collections import Counter

from app.mcp.client import schema_hash
from app.mcp.config import MCPConfig
from tests.fixtures.raw_mcp import RawMCPServer, _event

TRICKLE_S = 2.0
FLOOD_BYTES = 2 * 1024 * 1024
_SCOPE = {"tenant": {"type": "string"}, "user": {"type": "string"}, "collections": {"type": "array"}}
SCHEMAS = {
    "register": {"type": "object", "properties": {
        "document_id": {"type": "string"}, "name": {"type": "string"}, "mime": {"type": "string"},
        "kind": {"type": "string"}, "size": {"type": "integer"}, "object_key": {"type": "string"},
        "collection": {"type": "string"}, **_SCOPE}},
    "status": {"type": "object", "properties": {"document_id": {"type": "string"}, **_SCOPE}},
    "search": {"type": "object", "properties": {
        "query": {"type": "string"}, "k": {"type": "integer"}, **_SCOPE}},
    "fetch": {"type": "object", "properties": {"refs": {"type": "array"}, **_SCOPE}},
}  # fmt: skip
LABEL = {"ar": "عملية مستندات", "en": "Document operation"}
INJECTED = {"tenant": "principal.tenant_id", "user": "principal.user_id", "collections": "scope.collections"}


def config(url: str, *, timeout_s: float, max_concurrency: int, server_id: str = "rag") -> MCPConfig:
    """config/mcp.yaml's shape for this server: the four operations, class transport."""
    tools = [
        {"name": name, "remote_name": name, "class": "transport",
         "risk": "write" if name == "register" else "read",
         "label": LABEL, "input_schema_sha256": schema_hash(schema), "injected": INJECTED}
        for name, schema in SCHEMAS.items()
    ]  # fmt: skip
    return MCPConfig.model_validate(
        {"servers": [{"id": server_id, "url_env": "URL", "internal": True, "profiles": ["default"],
                      "timeout_s": timeout_s, "max_concurrency": max_concurrency, "tools": tools}]}
    )  # fmt: skip


class DocumentOpsMCPServer(RawMCPServer):
    def __init__(self) -> None:
        super().__init__()
        self.mode: dict[str, object] = dict.fromkeys(SCHEMAS, "ok")
        self.open_requests: Counter[str] = Counter()
        self.peak: Counter[str] = Counter()

    def reset(self) -> None:
        self.mode = dict.fromkeys(SCHEMAS, "ok")
        self.peak = Counter(self.open_requests)
        self.calls.clear()

    async def _until_disconnect(self, receive, seconds: float | None) -> bool:
        disconnected = asyncio.ensure_future(receive())
        try:
            done, _ = await asyncio.wait([disconnected], timeout=seconds)
            return bool(done)
        finally:
            disconnected.cancel()

    async def app(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST":
            return await super().app(scope, receive, send)
        body = await self._body(receive)
        message = json.loads(body or b"{}")
        rid, method = message.get("id"), message.get("method")
        if method == "tools/list":
            tools = [{"name": name, "inputSchema": schema} for name, schema in SCHEMAS.items()]
            listed = {"jsonrpc": "2.0", "id": rid, "result": {"tools": tools}}
            await self._json(send, json.dumps(listed).encode())
            return
        if method != "tools/call":
            replayed = False

            async def replay():
                nonlocal replayed
                if not replayed:
                    replayed = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()

            await super().app(scope, replay, send)
            return
        name = message["params"]["name"]
        arguments = message["params"].get("arguments") or {}
        self.calls[name] += 1
        self.open_requests[name] += 1
        self.peak[name] = max(self.peak[name], self.open_requests[name])
        try:
            await self._operation(name, arguments, rid, receive, send)
        finally:
            self.open_requests[name] -= 1

    async def _operation(self, name: str, arguments: dict, rid, receive, send) -> None:
        mode = self.mode.get(name, "ok")
        if mode == "hung":
            await self._until_disconnect(receive, None)
            return
        if mode == "fail":
            error = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32603, "message": "operation failed"}}
            await self._json(send, json.dumps(error).encode())
            return
        if isinstance(mode, int | float) and not isinstance(mode, bool):
            if await self._until_disconnect(receive, float(mode)):
                return
            mode = "ok"
        if mode == "trickle":
            await self._sse_start(send)
            disconnected = asyncio.ensure_future(receive())
            try:
                while not disconnected.done():
                    await send({"type": "http.response.body", "body": b":", "more_body": True})
                    await asyncio.wait([disconnected], timeout=TRICKLE_S)
            finally:
                disconnected.cancel()
            return
        structured = self._result(name, arguments)
        result = {"content": [{"type": "text", "text": "{}"}], "structuredContent": structured}
        done = {"jsonrpc": "2.0", "id": rid, "result": result}
        if mode == "flood":
            await self._sse_start(send)
            sent = 0
            while sent < FLOOD_BYTES:
                chunk = b"".join(
                    _event({"jsonrpc": "2.0", "method": "notifications/progress",
                            "params": {"progressToken": 1, "progress": sent + i}})
                    for i in range(100)
                )  # fmt: skip
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
                sent += len(chunk)
            await send({"type": "http.response.body", "body": _event(done)})
            return
        await self._json(send, json.dumps(done).encode())

    @staticmethod
    def _result(name: str, arguments: dict) -> dict:
        if name == "register":
            return {"registered": True}
        if name == "status":
            return {"found": True, "status": "indexed", "reason": None, "pages": 1, "warnings": []}
        if name == "fetch":
            return {"texts": {ref: f"نص المقطع {ref}" for ref in arguments.get("refs") or []}}
        return {"hits": []}


async def connected(server: "DocumentOpsMCPServer", *, hash_key: str, timeout_s: float, max_concurrency: int,
                    max_result_bytes: int = 256 * 1024):  # fmt: skip
    """An MCP client connected to `server` and the MCP document backend over it, through a broker's
    transport (ADR-0230): (client, backend). The caller stops the client."""
    from app.guardrails.engine import GuardEngine
    from app.mcp.client import MCPClient
    from app.mcp.config import resolve
    from app.rag.mcp import MCPDocumentBackend
    from app.tools.broker import ToolBroker
    from app.tools.registry import ToolRegistry

    settings = config(server.url, timeout_s=timeout_s, max_concurrency=max_concurrency)
    entry = settings.servers[0].model_copy(update={"max_result_bytes": max_result_bytes})
    settings = settings.model_copy(update={"servers": [entry]})
    client = MCPClient(
        resolve(settings, profile="default", allow_external=False, environ={"URL": server.url}),
        hash_key=hash_key,
        client_version="test",
    )
    client.start()
    assert await client.wait_connected(10), client.health()
    assert client.servers[entry.id].disabled == {}, client.servers[entry.id].disabled
    return client, MCPDocumentBackend(
        ToolBroker(ToolRegistry(), GuardEngine(), mcp=client).transport, entry.id
    )
