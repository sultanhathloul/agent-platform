"""A raw MCP server that answers slowly and counts the requests open at it (fix session 2026-10-19,
review round 2, C1 and C2; ADR-0244 to ADR-0246). Like tests/fixtures/raw_mcp.py it runs uvicorn
in a thread of its own. Its tools:

- `slow {seconds}` → `{"found": true}` after `seconds`, unless the client ends the request first
  (a server that notices its client gone, as an SDK server does);
- `status {seconds}` → the same as `slow`, for a document backend's status operation;
- `quick {}` → `{"ok": true}` at once;
- `ask {}` → sends a sampling request on the call's SSE stream, then holds the call's own answer
  for ASK_HOLD_S. An answer the client would POST to the sampling request is held for
  REPLY_HOLD_S (the reviewer's case of C2) and counted in `replies`.

A request is open from the moment it arrives until its response is complete or its client has
disconnected: `open_requests` counts them now, `peak` at worst since `reset_peak`."""

import asyncio
import json

from tests.fixtures.raw_mcp import RawMCPServer, _event

SLOW_SCHEMA = {"type": "object", "properties": {"seconds": {"type": "number"}}, "required": ["seconds"]}
EMPTY_SCHEMA = {"type": "object", "properties": {}}
SCHEMAS = {"slow": SLOW_SCHEMA, "status": SLOW_SCHEMA, "quick": EMPTY_SCHEMA, "ask": EMPTY_SCHEMA}
ASK_HOLD_S = 3.0
REPLY_HOLD_S = 8.0


class SlowMCPServer(RawMCPServer):
    def __init__(self) -> None:
        super().__init__()
        self.open_requests = 0
        self.peak = 0
        self.replies = 0  # answers the client sent to the server's own requests

    def reset_peak(self) -> None:
        self.peak = self.open_requests

    async def app(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            return await super().app(scope, receive, send)
        self.open_requests += 1
        self.peak = max(self.peak, self.open_requests)
        try:
            await self._handle(scope, receive, send)
        finally:
            self.open_requests -= 1

    async def _until_disconnect(self, receive, seconds: float) -> bool:
        """Waits `seconds`; True when the client disconnected first."""
        disconnected = asyncio.ensure_future(receive())
        try:
            done, _ = await asyncio.wait([disconnected], timeout=seconds)
            return bool(done)
        finally:
            disconnected.cancel()

    async def _handle(self, scope, receive, send) -> None:
        if scope["method"] != "POST":
            return await super().app(scope, receive, send)
        body = await self._body(receive)
        message = json.loads(body or b"{}")
        if "method" not in message and "id" in message:  # the client's answer to the server's request
            self.replies += 1
            await self._until_disconnect(receive, REPLY_HOLD_S)
            await send({"type": "http.response.start", "status": 202, "headers": []})
            await send({"type": "http.response.body", "body": b""})
            return
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
        self.calls[name] += 1
        if name in ("slow", "status"):
            seconds = float(message["params"].get("arguments", {}).get("seconds", 0))
            if await self._until_disconnect(receive, seconds):
                return  # the client ended the request
            result = {"content": [{"type": "text", "text": "{}"}], "structuredContent": {"found": True}}
            await self._json(send, json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}).encode())
        elif name == "quick":
            result = {"content": [{"type": "text", "text": "{}"}], "structuredContent": {"ok": True}}
            await self._json(send, json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}).encode())
        elif name == "ask":
            await self._sse_start(send)
            sampling = {
                "jsonrpc": "2.0",
                "id": 900,
                "method": "sampling/createMessage",
                "params": {
                    "messages": [{"role": "user", "content": {"type": "text", "text": "hi"}}],
                    "maxTokens": 5,
                },
            }
            await send({"type": "http.response.body", "body": _event(sampling), "more_body": True})
            if await self._until_disconnect(receive, ASK_HOLD_S):
                return
            result = {"content": [{"type": "text", "text": "{}"}], "structuredContent": {"asked": True}}
            answer = _event({"jsonrpc": "2.0", "id": rid, "result": result})
            await send({"type": "http.response.body", "body": answer})
