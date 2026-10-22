"""The platform's MCP client (phase 05, C1; ADR-0203 to ADR-0210, ADR-0244, ADR-0246). A pool of
sessions per enabled server of config/mcp.yaml, over Streamable HTTP with the official SDK, behind
the broker: no capability imports this module (ADR-0197).

- Pinning (I5): on every connection the server's tools are listed and each allowlisted tool's
  input schema is hashed and compared with `input_schema_sha256`. A mismatch or a missing tool
  disables that tool only (fail closed, logged, in /v1health); a tool the server lists but the
  allowlist does not name is never known to the platform (D4).
- Resilience (I8, ADR-0237, ADR-0244): a semaphore per server (`max_concurrency`), a deadline per call and
  a circuit breaker (5 consecutive failures open it for 30 s; then one trial call). Waiting for
  the semaphore past `timeout_s` fails `tool_busy`, which says nothing about the server and leaves
  the breaker as it was. A call that cannot be made fails at once with `tool_unavailable`; one
  that takes longer than `timeout_s` from send to its last byte is cut by the transport and fails
  `tool_timeout`. Only the server's own outcome decides the trial call: a success closes the
  breaker, a server error or the call's own timeout reopens it; a call its caller cancelled
  releases the trial without a verdict, and the breaker stays half open. Nothing here touches any
  other server or turn (G7). The connection is kept in a task of its own, which reconnects with
  backoff, so a server that is down never stops the agent from starting.
- Sessions (ADR-0246): up to `max_concurrency` per server, each used by one call at a time; a call
  takes a session with its semaphore slot and returns both when its HTTP request has ended. A call
  that is cancelled, times out or fails closes its session, which aborts its request, before its
  slot is returned, and a fresh session takes its place: requests open at a server never exceed
  `max_concurrency`. Sessions carry no per-user state (I9): every call carries what it needs; when
  the server no longer knows a session, the tools are pinned again on a new one and the call is
  tried once more; when it no longer knows that one either (it restarted again), the call fails
  `tool_unavailable` with no verdict and that generation ends too (ADR-0272).
- Background work (ADR-0265): a call in a detached context (a registration past the upload's
  answer, a status read past its bound) takes one of the server's background slots, at most half
  of max_concurrency, before a slot, so callers always find some. Its slot wait is bounded on its
  own, as a caller's is, and once it has a slot its attempt has the full timeout_s (ADR-0274): a
  wait behind other users' work is never taken from what the server is given.
- Bounds (I6, ADR-0259), each cut to what remains of the caller's deadline (app/resilience/
  deadline.py): the wait for a slot is at most min(timeout_s, remaining − timeout_s − 1), so the
  call still has its timeout_s once it has one; when no wait is left and no slot is free the call
  fails `tool_busy` at once, the breaker untouched. One attempt deadline, min(timeout_s,
  remaining − 0.5), covers opening a session and the request, the I9 re-send and its wait for the
  next generation included; it ending by the caller's deadline is no verdict (as a cancellation,
  ADR-0244). The transport's wall-clock deadline is timeout_s from send (transport.py) and the
  SDK's own read timeout timeout_s + 1, so the SDK never decides.
- Results (I7): the structured content when present, else the text parts; images, audio and
  resource links are dropped with a note; the body is capped while it is read (transport.py).
- Observability (G8): the `mcp.call` span carries the server, the tool, the status, the latency,
  the byte sizes and an HMAC of the arguments under CONTENT_HASH_KEY, never the text of either.
- Server-initiated features (I2): the session declares no sampling, elicitation or roots, and the
  transport drops any such request unanswered (`server_request_dropped`); resources and prompts
  are never read; a tools changed notification is ignored (the allowlist and the pins decide, not
  the server)."""

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
from app.resilience import deadline
from app.tools.spec import ToolFailure

log = logging.getLogger(__name__)

UNAVAILABLE = "tool_unavailable"
TIMEOUT = "tool_timeout"
TOOL_ERROR = "tool_error"
BUSY = "tool_busy"
BREAKER_FAILURES = 5
BREAKER_OPEN_S = 30.0
_BACKOFF_MAX_S = 30.0
# I6: what an attempt leaves of its caller's deadline, and the SDK's read timeout past the
# transport's deadline (timeout_s), so the transport decides.
_ATTEMPT_MARGIN_S = 0.5
_SDK_READ_MARGIN_S = 1.0
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

    def admit(self) -> str | None:
        """`call` in the closed state, `trial` for the one call let through when half open, None
        when the call is refused. A trial ends by `success`, `failure` or, cancelled, `release`."""
        state = self.state
        if state == "closed":
            return "call"
        if state == "half_open" and not self._trial:
            self._trial = True
            return "trial"
        return None

    def allow(self) -> bool:
        return self.admit() is not None

    def success(self) -> None:
        self.failures, self.opened_at, self._trial = 0, None, False

    def release(self) -> None:
        """Ends the trial without a verdict (its caller cancelled it): the breaker stays half open
        and the next call may be the trial (ADR-0244)."""
        self._trial = False

    @property
    def trial_in_progress(self) -> bool:
        return self._trial

    def failure(self) -> None:
        self.failures += 1
        if self._trial or self.failures >= BREAKER_FAILURES:
            self.opened_at, self._trial = self._clock(), False


class _Failed(Exception):
    """A call that failed, by its content-free code, and whether that says the server failed: True
    or False is the server's own outcome on this attempt; None is no verdict, the attempt never
    reached the server (ADR-0249)."""

    def __init__(self, code: str, server_failed: bool | None):
        super().__init__(code)
        self.code = code
        self.server_failed = server_failed


class _Unreachable(Exception):
    """A session could not be opened: the server is not reachable."""


class _NoGeneration(Exception):
    """No generation is connected: another call ended it and the next is being opened, or the
    server is down. Says nothing of this call's own outcome (ADR-0249)."""


# How long `stop` lets its sessions say goodbye to their servers before it cancels them.
_STOP_GRACE_S = 1.0


class _Session:
    """One MCP session, used by one call at a time (ADR-0246). The SDK's contexts must be entered
    and left in one task, so each session lives in a task of its own; closing it leaves those
    contexts, which cancels the request still open on it (the HTTP connection is closed, so the
    server sees the request end), and then tells the server the session is over."""

    def __init__(self, connection: "ServerConnection", generation: int):
        self.generation = generation
        self.session: ClientSession | None = None
        self.transport: CappedTransport | None = None
        self._connection = connection
        self._opened = asyncio.Event()
        self._close = asyncio.Event()
        self.task = asyncio.create_task(self._run(), name=f"mcp:{connection.entry.id}:session")

    async def opened(self) -> ClientSession:
        """The initialised session; raises _Unreachable when it could not be opened."""
        await self._opened.wait()
        if self.session is None:
            raise _Unreachable()
        return self.session

    def request_close(self) -> None:
        self._close.set()

    async def close(self) -> None:
        """Returns once the session's requests have ended and its connection is closed."""
        self._close.set()
        await asyncio.wait([self.task])

    async def _run(self) -> None:
        connection, entry = self._connection, self._connection.entry
        transport = CappedTransport(entry.max_result_bytes, entry.timeout_s)
        token = connection.resolved.token
        # The HTTP bound is a little past the call's own, so a call ends as tool_timeout first and
        # its request is then let go by the transport, never by the session (G7).
        http = httpx.AsyncClient(
            transport=transport,
            headers={"Authorization": f"Bearer {token}"} if token else {},
            timeout=httpx.Timeout(entry.timeout_s + 1.0),
            follow_redirects=False,
        )
        client_info = types.Implementation(name="agent-platform", version=connection.version)
        session_id: str | None = None
        try:
            async with http:
                try:
                    # The session is ended by `_terminate` below, after the requests still open on
                    # it have been cancelled; the SDK would send its DELETE while they still run.
                    async with (
                        streamable_http_client(
                            connection.resolved.url, http_client=http, terminate_on_close=False
                        ) as (read, write, get_session_id),
                        ClientSession(
                            read,
                            write,
                            read_timeout_seconds=timedelta(seconds=entry.timeout_s + _SDK_READ_MARGIN_S),
                            client_info=client_info,
                            message_handler=_ignore_server_messages,
                        ) as session,
                    ):
                        await asyncio.wait_for(session.initialize(), entry.timeout_s)
                        session_id = get_session_id()
                        connection.sessions += 1
                        self.session, self.transport = session, transport
                        self._opened.set()
                        await self._close.wait()
                finally:
                    self.session = None
                    if session_id:
                        await self._terminate(http, session_id)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 (a session that fails ends; its caller is told)
            log.warning("mcp server %s: session ended (%s)", entry.id, _kinds(exc))
        finally:
            self.session = None
            self._opened.set()
            connection.forget(self)

    async def _terminate(self, http: httpx.AsyncClient, session_id: str) -> None:
        """Tells the server the session is over, within the server's timeout_s."""
        with contextlib.suppress(Exception):
            async with asyncio.timeout(self._connection.entry.timeout_s):
                await http.delete(self._connection.resolved.url, headers={"mcp-session-id": session_id})


@dataclass
class _Lease:
    """The session a call holds, and whether the call ended cleanly (an answer from the server):
    only then does the session go back to the pool."""

    pooled: _Session | None = None
    clean: bool = False
    background: bool = False  # it holds one of the server's background slots too (ADR-0265)


class ServerConnection:
    """One server: its pool of sessions, its pins, semaphore and breaker."""

    def __init__(self, resolved: Resolved, *, hash_key: str, client_version: str):
        self.resolved = resolved
        self.entry: ServerEntry = resolved.entry
        self._hash_key = hash_key
        self.version = client_version
        self._semaphore = asyncio.Semaphore(self.entry.max_concurrency)
        # Background work (a registration past the upload's answer, a status read past its bound)
        # takes at most half of the slots, so callers always find some (ADR-0265).
        self._background = asyncio.Semaphore(max(1, self.entry.max_concurrency // 2))
        self.breaker = Breaker()
        # The pool (ADR-0246): sessions of the current generation that no call holds. With the
        # sessions calls hold, never more than max_concurrency: a call takes one with its slot.
        self._idle: list[_Session] = []
        self._live: set[_Session] = set()  # every session not yet ended: idle, held or closing
        self._closing: set[asyncio.Task] = set()
        # A generation is the sessions opened since the tools were last pinned; a call that finds
        # the server unreachable, or not knowing its session, ends it (`_reset`).
        self._generation = 0
        self._down = asyncio.Event()
        self._task: asyncio.Task | None = None
        self.connected = asyncio.Event()
        self.sessions = 0  # sessions initialised so far (I9)
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
        for pooled in self._live:
            pooled.request_close()
        tasks = [*self._closing, *(pooled.task for pooled in self._live)]
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=_STOP_GRACE_S)
            for task in pending:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _keep_connected(self) -> None:
        attempt = 0
        while True:
            try:
                await self._connect_once()
                attempt = 0  # the generation ended after it had worked: reconnect at once
            except asyncio.CancelledError:
                raise
            except BaseException as exc:  # noqa: BLE001 (any failure of a connection is retried)
                log.warning("mcp server %s: connection failed (%s)", self.entry.id, _kinds(exc))
                attempt += 1
            finally:
                self._disconnect()
            if attempt:
                delay = min(_BACKOFF_MAX_S, 0.5 * 2 ** min(attempt - 1, 6))
                await asyncio.sleep(delay * (0.8 + 0.4 * random.random()))

    async def _connect_once(self) -> None:
        """Opens a generation's first session, pins the tools on it, puts it in the pool and waits
        until a call ends the generation."""
        self._down.clear()
        first = self._open()
        try:
            session = await first.opened()
            listed = await asyncio.wait_for(_list_tools(session), self.entry.timeout_s)
        except BaseException:
            self._spawn(first.close())
            raise
        self._pin(listed)
        self._idle.append(first)
        self.connected.set()
        log.info(
            "mcp server %s: connected, %d tool(s) verified, %d disabled",
            self.entry.id,
            len(self.verified),
            len(self.disabled),
        )
        await self._down.wait()

    def _disconnect(self) -> None:
        """Ends the generation: its idle sessions are closed and its pins forgotten. Sessions calls
        still hold are closed when those calls end (`_finish`)."""
        self.connected.clear()
        self._generation += 1
        idle, self._idle = self._idle, []
        for pooled in idle:
            self._spawn(pooled.close())
        for name in self.verified:
            self.disabled[name] = "not_connected"
        self.verified = {}

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

    def _reset(self, generation: int) -> None:
        """Ends `generation` when it is still the current one; the connection task opens and pins
        the next. The connected flag is cleared here, at once, so a call waiting for the next
        generation never takes a session of this one; a generation already ended is left alone."""
        if generation != self._generation:
            return
        self.connected.clear()
        self._down.set()

    # ------------------------------------------------------------------ the pool

    def _open(self) -> _Session:
        pooled = _Session(self, self._generation)
        self._live.add(pooled)
        return pooled

    def forget(self, pooled: _Session) -> None:
        """Called by a session when it has ended."""
        self._live.discard(pooled)

    def _spawn(self, work) -> None:
        task = asyncio.ensure_future(work)
        self._closing.add(task)
        task.add_done_callback(self._closing.discard)

    async def _take(self, lease: _Lease) -> None:
        """Gives the call an idle session of the current generation, or opens one; raises
        _Unreachable when the server cannot be reached."""
        while self._idle:
            pooled = self._idle.pop()
            if pooled.session is not None:
                lease.pooled = pooled
                return
        if not self.connected.is_set():
            raise _NoGeneration()  # never _Unreachable: this call saw no server (ADR-0249)
        lease.pooled = self._open()  # held by the call before it waits, so it is never lost
        await lease.pooled.opened()

    def _finish(self, lease: _Lease) -> None:
        """Returns a call's slot. A session after an answer goes back to the pool at once; any
        other end (a cancellation, a timeout, a failure) may have left its request open at the
        server, so the session is closed first, which ends that request, and only then is the slot
        returned: requests open at a server never exceed max_concurrency (ADR-0246)."""
        pooled = lease.pooled
        if pooled is None:
            self._release_slot(lease)
        elif lease.clean:
            if pooled.generation == self._generation and pooled.session is not None:
                self._idle.append(pooled)
            else:
                self._spawn(pooled.close())
            self._release_slot(lease)
        else:
            self._spawn(self._replace(pooled, lease))

    def _release_slot(self, lease: _Lease) -> None:
        self._semaphore.release()
        if lease.background:
            self._background.release()

    async def _replace(self, pooled: _Session, lease: _Lease) -> None:
        """Closes a session a call left, then, within the call's slot, opens a fresh one in its
        place when its generation is still current; the slot is returned last."""
        try:
            await pooled.close()
            if pooled.generation == self._generation and self.connected.is_set():
                fresh = self._open()
                try:
                    await fresh.opened()
                except _Unreachable:
                    return
                if fresh.generation == self._generation:
                    self._idle.append(fresh)
                else:
                    await fresh.close()
        finally:
            self._release_slot(lease)

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
        if tool.name not in self.verified or not self.connected.is_set():
            raise ToolFailure(UNAVAILABLE)
        if self.breaker.state == "open":
            raise ToolFailure(UNAVAILABLE)
        lease = _Lease(background=deadline.detached())
        await self._slot(lease, observed)
        try:
            admitted = self.breaker.admit()
            if admitted is None:
                raise ToolFailure(UNAVAILABLE)
            trial = admitted == "trial"
            decided = False
            try:
                result = await self._attempt(tool, arguments, observed, lease)
                decided = True
                self.breaker.success()
                return result
            except _Failed as failed:
                if failed.server_failed is None:
                    # The attempt never reached the server (a generation another call ended, a
                    # wait for the next one): no verdict, as a cancellation (ADR-0249).
                    raise ToolFailure(failed.code) from None
                decided = True
                if failed.server_failed or trial:
                    self.breaker.failure()
                else:
                    self.breaker.success()  # the server is up and answered this call
                raise ToolFailure(failed.code) from None
            finally:
                if trial and not decided:
                    # Cancelled by its caller (a status bound, a time limit, a client gone): that
                    # says nothing about the server. The trial is released without a verdict and
                    # the next call may be the trial (ADR-0244).
                    self.breaker.release()
        finally:
            self._finish(lease)

    async def _slot(self, lease: _Lease, observed: dict[str, str | int | bool]) -> None:
        """Takes a slot for the call; raises ToolFailure(BUSY), the breaker untouched (nothing
        reached the server), when none frees up in time. A call waits only as long as leaves it its
        own timeout_s (I6); a free slot is taken at once. Background work takes one of the
        background slots first (ADR-0265) and has the same guarantee: its wait is bounded on its
        own, never taken from its attempt (ADR-0274)."""
        timeout_s = self.entry.timeout_s
        if not self._semaphore.locked() and not (lease.background and self._background.locked()):
            if lease.background:
                await self._background.acquire()
            await self._semaphore.acquire()  # a slot is free: taken at once
            return
        observed["queued"] = True
        # Wait only as long as leaves the call its own timeout_s (I6); no wait left: busy now.
        wait_s = deadline.bounded(timeout_s, margin_s=timeout_s + 1.0)
        if wait_s <= 0:
            raise ToolFailure(BUSY)  # the breaker is untouched: nothing reached the server
        held_background = False
        try:
            async with asyncio.timeout(wait_s):
                if lease.background:
                    await self._background.acquire()
                    held_background = True
                await self._semaphore.acquire()
        except TimeoutError:
            if held_background:
                self._background.release()
            raise ToolFailure(BUSY) from None  # the queue was full: not a server failure
        except BaseException:
            if held_background:
                self._background.release()
            raise

    async def _attempt(
        self,
        tool: ToolEntry,
        arguments: dict[str, Any],
        observed: dict[str, str | int | bool],
        lease: _Lease,
    ) -> CallResult:
        """One call on a session of the pool, re-sent once on a new generation's session when the
        server no longer knows this one (I9), within timeout_s. Raises _Failed with a content-free
        code."""
        waiting = False  # waiting for the next generation: a timeout then is no verdict (ADR-0249)
        resent = False  # the I9 re-send in flight: sent after the attempt began, so held less
        try:
            # One deadline for the attempt: the session, the request and the I9 re-send (I6).
            with deadline.within(self.entry.timeout_s, margin_s=_ATTEMPT_MARGIN_S):
                await self._take(lease)
                try:
                    result = await self._send(lease.pooled, tool, arguments, observed)
                except McpError as exc:
                    if _SESSION_GONE not in str(exc.error.message):
                        raise
                    # I9: the server restarted. Its 404 ended the request, so nothing is open on
                    # the session; the generation ends, and the call is sent on the next one.
                    gone, lease.pooled = lease.pooled, None
                    self._reset(gone.generation)
                    self._spawn(gone.close())
                    waiting = True
                    await self.connected.wait()
                    waiting = False
                    resent = True
                    await self._take(lease)
                    result = await self._send(lease.pooled, tool, arguments, observed)
                lease.clean = True
                return result
        except deadline.DeadlineExceeded as exc:
            # A timeout is the server's outcome only when the server held the request for its full
            # timeout_s from the moment it was sent; the caller's deadline, a wait for the next
            # generation, or a re-send sent after the attempt began says nothing of the server
            # (ADR-0244, ADR-0249, ADR-0274).
            raise _Failed(TIMEOUT, None if exc.inherited or waiting or resent else True) from None
        except TimeoutError:
            raise _Failed(TIMEOUT, None if waiting else True) from None
        except _NoGeneration:
            raise _Failed(UNAVAILABLE, None) from None
        except McpError as exc:
            message = str(exc.error.message)
            if _SESSION_GONE in message:
                # The I9 re-send's session is gone too: the server restarted again after the next
                # generation was opened. A 404 is an answer from a server that is up, so no verdict
                # (ADR-0272); the generation the re-send was sent on ends, when still current.
                self._reset(self._generation_of(lease))
                raise _Failed(UNAVAILABLE, None) from None
            if message == TOO_LARGE:
                raise _Failed(TOO_LARGE, False) from None  # the server answered; the answer was too big
            if message == TRANSPORT_TIMEOUT:
                raise _Failed(TIMEOUT, True) from None
            if message == TRANSPORT_FAILED:
                # The server is not reachable: the generation is likely gone with it. Reconnecting
                # (with backoff) is the connection task's; this call fails at once.
                self._reset(self._generation_of(lease))
                raise _Failed(UNAVAILABLE, True) from None
            if exc.error.code == httpx.codes.REQUEST_TIMEOUT:
                raise _Failed(TIMEOUT, True) from None
            if exc.error.code in (types.INVALID_PARAMS, types.METHOD_NOT_FOUND, types.INTERNAL_ERROR):
                lease.clean = True
                raise _Failed(TOOL_ERROR, False) from None  # the server is up and refused or failed it
            raise _Failed(UNAVAILABLE, True) from None
        except _Unreachable:
            self._reset(self._generation_of(lease))
            raise _Failed(UNAVAILABLE, True) from None
        except ToolFailure as failure:
            raise _Failed(failure.code, True) from None
        except Exception as exc:  # transport, closed stream, protocol: the server is not reachable
            log.warning("mcp server %s: call failed (%s)", self.entry.id, type(exc).__name__)
            self._reset(self._generation_of(lease))
            raise _Failed(UNAVAILABLE, True) from None

    def _generation_of(self, lease: _Lease) -> int:
        return lease.pooled.generation if lease.pooled is not None else self._generation

    async def _send(
        self,
        pooled: _Session | None,
        tool: ToolEntry,
        arguments: dict[str, Any],
        observed: dict[str, str | int | bool],
    ) -> CallResult:
        session = pooled.session if pooled is not None else None
        transport = pooled.transport if pooled is not None else None
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
    """Notifications (tools changed, logging, progress) are ignored (I2). Requests the server
    sends (sampling, elicitation, roots) never get here: the transport drops them unanswered
    (ADR-0246), and the session declares none of those capabilities."""
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
