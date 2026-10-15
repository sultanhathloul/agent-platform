"""A test-only MCP server (phase 05, C1), run in the test's event loop with uvicorn on a free port
of the loopback interface, so the platform's client is tested over real Streamable HTTP with the
official SDK. Its tools:

- `echo {text}` → structured content `{echo}` and a text part;
- `scoped {query, tenant, user, collections}` → what it received, so a test sees the injected
  fields the broker filled;
- `slow {seconds}` → sleeps, then answers;
- `big {size}` → a text of `size` characters;
- `mixed {}` → a text part, an image and an embedded resource;
- `fails {}` → an error result;
- `admin_secret {}` → a tool no allowlist names (D4).

Every call is counted by tool name, so a test can prove a refused call never reached the server.
`stop()` and `start()` take it down and up again on the same port (reconnect and session tests)."""

import asyncio
import base64
import socket
from collections import Counter
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.types import EmbeddedResource, ImageContent, TextContent, TextResourceContents
from pydantic import BaseModel


class Echo(BaseModel):
    echo: str


class Scoped(BaseModel):
    tenant: str
    user: str
    collections: list[str]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class FixtureServer:
    def __init__(self, port: int | None = None):
        self.port = port or free_port()
        self.calls: Counter[str] = Counter()
        self.received: list[dict[str, Any]] = []
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    def _app(self):
        mcp = FastMCP("fixture", host="127.0.0.1", port=self.port, log_level="WARNING")
        calls, received = self.calls, self.received

        @mcp.tool()
        def echo(text: str) -> Echo:
            """Echoes the text."""
            calls["echo"] += 1
            return Echo(echo=text)

        @mcp.tool()
        def scoped(query: str, tenant: str, user: str, collections: list[str]) -> Scoped:
            """Returns what it received."""
            calls["scoped"] += 1
            received.append({"query": query, "tenant": tenant, "user": user, "collections": collections})
            return Scoped(tenant=tenant, user=user, collections=collections)

        @mcp.tool()
        async def slow(seconds: float) -> str:
            """Sleeps."""
            calls["slow"] += 1
            await asyncio.sleep(seconds)
            return "done"

        @mcp.tool()
        def big(size: int) -> str:
            """A long text."""
            calls["big"] += 1
            return "x" * size

        @mcp.tool()
        def mixed() -> list:
            """Text, an image and an embedded resource."""
            calls["mixed"] += 1
            pixel = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()
            return [
                TextContent(type="text", text="visible text"),
                ImageContent(type="image", data=pixel, mimeType="image/png"),
                EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(uri="file:///secret", text="hidden resource text"),
                ),
            ]

        @mcp.tool()
        def fails() -> str:
            """Always fails."""
            calls["fails"] += 1
            raise ValueError("server-side failure with private detail")

        @mcp.tool()
        def admin_secret() -> str:
            """A tool no allowlist names."""
            calls["admin_secret"] += 1
            return "never"

        return mcp.streamable_http_app()

    async def start(self) -> None:
        config = uvicorn.Config(
            self._app(), host="127.0.0.1", port=self.port, log_level="warning", lifespan="on"
        )
        self._server = uvicorn.Server(config)
        self._task = asyncio.create_task(self._server.serve())
        for _ in range(100):
            if self._server.started:
                return
            await asyncio.sleep(0.05)
        raise RuntimeError("fixture MCP server did not start")

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
            self._server.force_exit = True
        if self._task is not None:
            await asyncio.wait_for(self._task, 10)
        self._server = self._task = None


async def tool_hashes(url: str) -> dict[str, str]:
    """The pinned hash of each tool the server lists, as `make mcp-hashes` prints them."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    from app.mcp.client import schema_hash

    async with streamable_http_client(url) as (read, write, _), ClientSession(read, write) as session:
        await session.initialize()
        listed = await session.list_tools()
        return {tool.name: schema_hash(tool.inputSchema) for tool in listed.tools}
