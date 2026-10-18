"""A minimal MCP Streamable HTTP server written byte by byte (fix session 2026-10-18, G7, G8): it
answers what the SDK's server never would, so the platform's transport is tested against
servers that misbehave. It runs uvicorn in a thread of its own, with its own event loop, so its
work never shows in the agent's event-loop lag that a test measures. Its tools:

- `quick {}` → `{"ok": true}` at once;
- `flood {}` → 1 MiB of `notifications/progress` events on the call's SSE stream, then the result;
- `trickle {}` → an SSE stream that sends one byte every 2 s and never the result;
- `garbled {}` → a JSON body that is not JSON and quotes a secret.

`open_streams` counts the SSE responses still being written: a connection the client closed ends
its stream within one byte's interval."""

import asyncio
import json
import threading
from collections import Counter

import uvicorn

from tests.fixtures.mcp_server import free_port

SECRET = "server-secret-text-9c41"
FLOOD_BYTES = 1024 * 1024
TRICKLE_INTERVAL_S = 2.0
TOOLS = ("quick", "flood", "trickle", "garbled")


def _event(message: dict) -> bytes:
    return b"event: message\ndata: " + json.dumps(message).encode() + b"\n\n"


class RawMCPServer:
    def __init__(self) -> None:
        self.port = free_port()
        self.calls: Counter[str] = Counter()
        self.open_streams = 0
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    async def _body(self, receive) -> bytes:
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                return body

    async def _json(self, send, payload: bytes, status: int = 200) -> None:
        headers = [(b"content-type", b"application/json"), (b"mcp-session-id", b"raw-session")]
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": payload})

    async def _sse_start(self, send) -> None:
        headers = [(b"content-type", b"text/event-stream"), (b"mcp-session-id", b"raw-session")]
        await send({"type": "http.response.start", "status": 200, "headers": headers})

    async def app(self, scope, receive, send) -> None:
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["method"] != "POST":
            await send({"type": "http.response.start", "status": 405, "headers": []})
            await send({"type": "http.response.body", "body": b""})
            return
        message = json.loads(await self._body(receive) or b"{}")
        if "method" not in message or "id" not in message:  # a notification or a response
            await send({"type": "http.response.start", "status": 202, "headers": []})
            await send({"type": "http.response.body", "body": b""})
            return
        rid, method = message["id"], message["method"]
        if method == "initialize":
            result = {
                "protocolVersion": message["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "raw", "version": "1"},
            }
            await self._json(send, json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}).encode())
            return
        if method == "tools/list":
            tools = [{"name": n, "inputSchema": {"type": "object", "properties": {}}} for n in TOOLS]
            await self._json(
                send, json.dumps({"jsonrpc": "2.0", "id": rid, "result": {"tools": tools}}).encode()
            )
            return
        name = message["params"]["name"]
        self.calls[name] += 1
        done = {"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": "ok"}]}}
        if name == "quick":
            await self._json(send, json.dumps(done).encode())
        elif name == "garbled":
            await self._json(send, f'{{"jsonrpc": "2.0", "id": {rid}, "result": {SECRET} not json'.encode())
        elif name == "flood":
            await self._sse_start(send)
            self.open_streams += 1
            try:
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
            finally:
                self.open_streams -= 1
        elif name == "trickle":
            await self._sse_start(send)
            self.open_streams += 1
            disconnected = asyncio.ensure_future(receive())
            try:
                while not disconnected.done():
                    await send({"type": "http.response.body", "body": b":", "more_body": True})
                    await asyncio.wait([disconnected], timeout=TRICKLE_INTERVAL_S)
            finally:
                self.open_streams -= 1
                disconnected.cancel()

    def start(self) -> None:
        config = uvicorn.Config(
            self.app, host="127.0.0.1", port=self.port, log_level="warning", lifespan="off", interface="asgi3"
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, name="raw-mcp", daemon=True)
        self._thread.start()
        for _ in range(200):
            if self._server.started:
                return
            threading.Event().wait(0.05)
        raise RuntimeError("raw MCP server did not start")

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
            self._server.force_exit = True
        if self._thread is not None:
            self._thread.join(10)
