"""The platform's MCP client (phase 05, C1; ADR-0203 to ADR-0210). One pooled session per enabled
server of config/mcp.yaml, over Streamable HTTP with the official SDK, behind the broker: no
capability imports this module (ADR-0197).

- Pinning (I5): on every connection the server's tools are listed and each allowlisted tool's
  input schema is hashed and compared with `input_schema_sha256`. A mismatch or a missing tool
  disables that tool only (fail closed, logged, in /v1health); a tool the server lists but the
  allowlist does not name is never known to the platform (D4).
- Resilience (I8): a semaphore per server (`max_concurrency`), a timeout per call, and a circuit
  breaker (5 consecutive failures open it for 30 s; then one trial call). A call that cannot be made
  fails at once with `tool_unavailable`, one that takes too long with `tool_timeout`; neither
  touches any other server or turn (G7). The connection is kept in a task of its own, which
  reconnects with backoff, so a server that is down never stops the agent from starting.
- Sessions carry no per-user state (I9): every call carries what it needs; an expired session is
  re-initialised and the call tried once more.
- Results (I7): the structured content when present, else the text parts; images, audio and
  resource links are dropped with a note; the body is capped while it is read (transport.py).
- Observability (G8): the `mcp.call` span carries the server, the tool, the status, the latency,
  the byte sizes and an HMAC of the arguments under CONTENT_HASH_KEY, never the text of either.
- Server-initiated features (I2): the session declares no sampling, elicitation or roots and
  answers any such request with an error; resources and prompts are never read; a tools changed
  notification is ignored (the allowlist and the pins decide, not the server)."""

import asyncio
import contextlib
import hashlib
import json
import logging
import random
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import httpx
from mcp import ClientSession, McpError, types
from mcp.client.streamable_http import streamable_http_client

from app.mcp.config import Resolved, ServerEntry, ToolEntry
from app.mcp.transport import CALL_META, TOO_LARGE, TRANSPORT_FAILED, TRANSPORT_TIMEOUT, CappedTransport
from app.observability.otel import tracer
from app.observability.redaction import content_hash
from app.tools.spec import ToolFailure

log = logging.getLogger(__name__)

UNAVAILABLE = "tool_unavailable"
TIMEOUT = "tool_timeout"
TOOL_ERROR = "tool_error"
BREAKER_FAILURES = 5
BREAKER_OPEN_S = 30.0
_BACKOFF_MAX_S = 30.0
# What the SDK reports when the server no longer knows the session (HTTP 404 on a request).
_SESSION_GONE = "Session terminated"


def schema_hash(schema: dict[str, Any]) -> str:
    """SHA-256 of a tool's input schema in canonical JSON (sorted keys, no spaces) (I5)."""
    canonical = json.dumps(schema, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class CallResult:
    content: str  # the text parts, or the structured content as JSON text
    data: Any = None  # the structured content, when the tool returned one
    is_error: bool = False
    dropped: list[str] = field(default_factory=list)  # kinds of parts left out: image, audio, ...
    # Content-free facts for the span and the audit entry (G8): server, sizes, argument HMAC.
    observed: dict[str, str | int | bool] = field(default_factory=dict)


class Breaker:
    """Five consecutive failures open the circuit for 30 s; then one trial call (I8)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self.failures = 0
        self.opened_at: float | None = None
        self._trial = False

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        return "half_open" if self._clock() - self.opened_at >= BREAKER_OPEN_S else "open"

    def allow(self) -> bool:
        state = self.state
        if state == "closed":
            return True
        if state == "half_open" and not self._trial:
            self._trial = True
            return True
        return False

    def success(self) -> None:
        self.failures, self.opened_at, self._trial = 0, None, False

    def failure(self) -> None:
        self.failures += 1
        if self._trial or self.failures >= BREAKER_FAILURES:
            self.opened_at, self._trial = self._clock(), False


class ServerConnection:
    """One server: its pooled session in a task of its own, its pins, semaphore and breaker."""

    def __init__(self, resolved: Resolved, *, hash_key: str, client_version: str):
        self.resolved = resolved
        self.entry: ServerEntry = resolved.entry
        self._hash_key = hash_key
        self._version = client_version
        self._semaphore = asyncio.Semaphore(self.entry.max_concurrency)
        self.breaker = Breaker()
        self._session: ClientSession | None = None
        self._transport: CappedTransport | None = None
        self._ended: asyncio.Event | None = None
        self._task: asyncio.Task | None = None
        self.connected = asyncio.Event()
        # Allowlisted tools whose pinned schema matched, by platform name: their input schema.
        self.verified: dict[str, dict[str, Any]] = {}
        # Allowlisted tools that cannot be called, by platform name: why (content-free). A server
        # this environment does not enable keeps every tool here, with the server's reason.
        why = "not_connected" if resolved.enabled else f"server_{resolved.reason}"
        self.disabled: dict[str, str] = {tool.name: why for tool in self.entry.tools}

    # ------------------------------------------------------------------ connection

    def start(self) -> None:
        if self._task is None and self.resolved.enabled:
            self._task = asyncio.create_task(self._keep_connected(), name=f"mcp:{self.entry.id}")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None

    async def _keep_connected(self) -> None:
        attempt = 0
        while True:
            try:
                await self._connect_once()
                attempt = 0  # the session ended after it had worked: reconnect at once
            except asyncio.CancelledError:
                raise
            except BaseException as exc:  # noqa: BLE001 (any failure of a connection is retried)
                log.warning("mcp server %s: connection failed (%s)", self.entry.id, _kinds(exc))
                attempt += 1
            finally:
                self._session = None
                self.connected.clear()
                for name in self.verified:
                    self.disabled[name] = "not_connected"
                self.verified = {}
            if attempt:
                delay = min(_BACKOFF_MAX_S, 0.5 * 2 ** min(attempt - 1, 6))
                await asyncio.sleep(delay * (0.8 + 0.4 * random.random()))

    async def _connect_once(self) -> None:
        transport = CappedTransport(self.entry.max_result_bytes)
        timeout = self.entry.timeout_s
        headers = {"Authorization": f"Bearer {self.resolved.token}"} if self.resolved.token else {}
        ended = asyncio.Event()
        # The HTTP bound is a little past the call's own, so a call ends as tool_timeout first and
        # its request is then let go by the transport, never by the session (G7).
        http = httpx.AsyncClient(
            transport=transport,
            headers=headers,
            timeout=httpx.Timeout(timeout + 1.0),
            follow_redirects=False,
        )
        async with (
            http,
            streamable_http_client(self.resolved.url, http_client=http) as (read, write, _session_id),
            ClientSession(
                read,
                write,
                read_timeout_seconds=timedelta(seconds=timeout),
                client_info=types.Implementation(name="agent-platform", version=self._version),
                message_handler=_ignore_server_messages,
            ) as session,
        ):
            await asyncio.wait_for(session.initialize(), timeout)
            listed = await asyncio.wait_for(_list_tools(session), timeout)
            self._pin(listed)
            self._session, self._transport, self._ended = session, transport, ended
            self.connected.set()
            log.info(
                "mcp server %s: connected, %d tool(s) verified, %d disabled",
                self.entry.id,
                len(self.verified),
                len(self.disabled),
            )
            await ended.wait()

    def _pin(self, listed: list[types.Tool]) -> None:
        """I5: each allowlisted tool is enabled only when the server lists it with the pinned
        input schema. Tools the allowlist does not name are not looked at (D4)."""
        remote = {tool.name: tool for tool in listed}
        verified: dict[str, dict[str, Any]] = {}
        disabled: dict[str, str] = {}
        for tool in self.entry.tools:
            found = remote.get(tool.remote_name)
            if found is None:
                disabled[tool.name] = "missing"
            elif schema_hash(found.inputSchema) != tool.input_schema_sha256:
                disabled[tool.name] = "schema_mismatch"
            else:
                verified[tool.name] = found.inputSchema
        for name, reason in disabled.items():
            log.warning("mcp server %s: tool %s disabled (%s)", self.entry.id, name, reason)
        self.verified, self.disabled = verified, disabled

    def _reset(self) -> None:
        """Ends the current session; the connection task re-initialises it."""
        if self._ended is not None:
            self._ended.set()

    # ------------------------------------------------------------------ calls

    def input_schema(self, tool: str) -> dict[str, Any] | None:
        return self.verified.get(tool)

    async def call(self, tool: ToolEntry, arguments: dict[str, Any]) -> CallResult:
        """Calls one allowlisted, verified tool; raises ToolFailure with a content-free code."""
        server = self.entry.id
        args_hmac = content_hash(
            json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False), self._hash_key
        )
        observed: dict[str, str | int | bool] = {"server": server, "args_hmac": args_hmac}
        started = time.monotonic()
        with tracer.start_as_current_span("mcp.call") as span:
            span.set_attribute("mcp.server", server)
            span.set_attribute("mcp.tool", tool.name)
            span.set_attribute("mcp.args_hmac", args_hmac)
            try:
                result = await self._call(tool, arguments, observed)
            except ToolFailure as failure:
                failure.observed.update(observed)
                span.set_attribute("mcp.status", failure.code)
                raise
            finally:
                span.set_attribute("mcp.latency_ms", int((time.monotonic() - started) * 1000))
                for key in ("request_bytes", "response_bytes"):
                    if key in observed:
                        span.set_attribute(f"mcp.{key}", observed[key])
            span.set_attribute("mcp.status", "error" if result.is_error else "ok")
            span.set_attribute("mcp.dropped", ",".join(result.dropped))
            result.observed.update(observed)
            return result

    async def _call(
        self, tool: ToolEntry, arguments: dict[str, Any], observed: dict[str, str | int | bool]
    ) -> CallResult:
        if tool.name not in self.verified or self._session is None:
            raise ToolFailure(UNAVAILABLE)
        if not self.breaker.allow():
            raise ToolFailure(UNAVAILABLE)
        if self._semaphore.locked():
            observed["queued"] = True
        try:
            async with asyncio.timeout(self.entry.timeout_s):
                async with self._semaphore:
                    try:
                        result = await self._send(tool, arguments, observed)
                    except McpError as exc:
                        if _SESSION_GONE not in str(exc.error.message):
                            raise
                        # I9: an expired session is re-initialised and the call tried once more.
                        self._reset()
                        await asyncio.wait_for(self.connected.wait(), self.entry.timeout_s)
                        result = await self._send(tool, arguments, observed)
        except TimeoutError:
            self.breaker.failure()
            raise ToolFailure(TIMEOUT) from None
        except McpError as exc:
            message = str(exc.error.message)
            if message == TOO_LARGE:
                self.breaker.success()  # the server answered; the answer was too big
                raise ToolFailure(TOO_LARGE) from None
            if message == TRANSPORT_TIMEOUT:
                self.breaker.failure()
                raise ToolFailure(TIMEOUT) from None
            if message == TRANSPORT_FAILED:
                # The server is not reachable: the session is likely gone with it. Reconnecting
                # (with backoff) is the connection task's; this call fails at once.
                self.breaker.failure()
                self._reset()
                raise ToolFailure(UNAVAILABLE) from None
            if exc.error.code == httpx.codes.REQUEST_TIMEOUT:
                self.breaker.failure()
                raise ToolFailure(TIMEOUT) from None
            if exc.error.code in (types.INVALID_PARAMS, types.METHOD_NOT_FOUND, types.INTERNAL_ERROR):
                self.breaker.success()  # the server is up and refused or failed this call
                raise ToolFailure(TOOL_ERROR) from None
            self.breaker.failure()
            raise ToolFailure(UNAVAILABLE) from None
        except ToolFailure:
            raise
        except Exception as exc:  # transport, closed stream, protocol: the server is not reachable
            log.warning("mcp server %s: call failed (%s)", self.entry.id, type(exc).__name__)
            self.breaker.failure()
            self._reset()
            raise ToolFailure(UNAVAILABLE) from None
        self.breaker.success()
        return result

    async def _send(
        self, tool: ToolEntry, arguments: dict[str, Any], observed: dict[str, str | int | bool]
    ) -> CallResult:
        session, transport = self._session, self._transport
        if session is None or transport is None:
            raise ToolFailure(UNAVAILABLE)
        call_id = uuid.uuid4().hex
        try:
            raw = await session.call_tool(tool.remote_name, arguments, meta={CALL_META: call_id})
        finally:
            sizes = transport.sizes.pop(call_id, None)
            if sizes is not None:
                observed["request_bytes"] = sizes.request_bytes
                observed["response_bytes"] = sizes.response_bytes
        return _normalize(raw)


def _kinds(exc: BaseException) -> str:
    """The type names of an exception and, for a group, of what it holds; never a message."""
    if isinstance(exc, BaseExceptionGroup):
        return ",".join(sorted({_kinds(inner) for inner in exc.exceptions}))
    return type(exc).__name__


def _normalize(raw: types.CallToolResult) -> CallResult:
    """I7: the structured content when present, else the text parts; other parts dropped."""
    texts: list[str] = []
    dropped: list[str] = []
    for part in raw.content:
        if isinstance(part, types.TextContent):
            texts.append(part.text)
        else:
            dropped.append(getattr(part, "type", type(part).__name__))
    structured = raw.structuredContent
    content = json.dumps(structured, ensure_ascii=False) if structured is not None else "\n".join(texts)
    return CallResult(content=content, data=structured, is_error=bool(raw.isError), dropped=dropped)


async def _list_tools(session: ClientSession) -> list[types.Tool]:
    tools: list[types.Tool] = []
    cursor: str | None = None
    for _ in range(50):  # a server that pages forever is cut off
        page = await session.list_tools(cursor)
        tools += page.tools
        cursor = page.nextCursor
        if not cursor:
            break
    return tools


async def _ignore_server_messages(message: Any) -> None:
    """Notifications (tools changed, logging, progress) are ignored (I2); requests the server
    sends (sampling, elicitation, roots) are answered with an error by the SDK, since the
    session declares none of those capabilities."""
    return None


class MCPClient:
    """Every server of config/mcp.yaml as this environment resolves it."""

    def __init__(self, resolved: list[Resolved], *, hash_key: str, client_version: str):
        self.resolved = resolved
        # Every allowlisted server, enabled or not: a manifest may declare the tools of a server
        # this environment does not run, and they fail `tool_unavailable` (D5, I16).
        self.servers: dict[str, ServerConnection] = {
            r.entry.id: ServerConnection(r, hash_key=hash_key, client_version=client_version)
            for r in resolved
        }

    def start(self) -> None:
        for server in self.servers.values():
            server.start()

    async def stop(self) -> None:
        await asyncio.gather(*(server.stop() for server in self.servers.values()))

    async def wait_connected(self, timeout: float) -> bool:
        """Tests and tools: whether every enabled server connected within `timeout` seconds."""
        try:
            async with asyncio.timeout(timeout):
                await asyncio.gather(
                    *(s.connected.wait() for s in self.servers.values() if s.resolved.enabled)
                )
            return True
        except TimeoutError:
            return False

    def health(self) -> list[dict[str, Any]]:
        """Each server and each disabled tool, content-free (I8): no URL, no token, no schema."""
        report = []
        for r in self.resolved:
            server = self.servers.get(r.entry.id)
            report.append(
                {
                    "id": r.entry.id,
                    "internal": r.entry.internal,
                    "enabled": r.enabled,
                    "reason": r.reason or None,
                    "connected": bool(server and server.connected.is_set()),
                    "circuit": server.breaker.state if server and r.enabled else None,
                    "disabled_tools": [
                        {"tool": f"{r.entry.id}.{name}", "reason": reason}
                        for name, reason in sorted((server.disabled if server else {}).items())
                    ],
                }
            )
        return report
