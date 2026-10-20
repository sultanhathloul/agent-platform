"""The HTTP layer under the MCP SDK's Streamable HTTP client (I2, I7, G7; ADR-0203, ADR-0208,
ADR-0236).

The SDK's httpx client sends every request through this transport, which bounds each one before
the SDK sees a byte of it:

- a deadline: `timeout_s` is wall-clock time for the whole request, from send to the last byte.
  At the deadline the read is cancelled and the connection closed, so a server that trickles
  holds nothing once the call has ended; the request ends as `tool_timeout`;
- a size cap: every byte of a response counts, the server's notifications and requests included.
  Past `max_bytes` (a `tools/call`; the protocol's own messages are bounded at 1 MiB) the
  connection is closed and the request ends as `tool_result_too_large`; nothing beyond the cap
  is read or parsed;
- an SSE response is passed to the SDK as it arrives, one complete event at a time, by an
  incremental parser: each byte is scanned once, and only the event being received is buffered,
  never a copy of the body per chunk. The stream ends after the event that answers the request,
  so a server that keeps it open costs nothing more. A server's own requests on that stream
  (sampling, elicitation, roots) are dropped unanswered, never handed to the SDK, and logged with
  the code `server_request_dropped` only: an answer would be a request of its own, which the
  server could hold (ADR-0246). The call goes on, bound by its own deadline;
- every way a request can end without its answer (deadline, cap, a broken or closed stream) is
  handed to the SDK as a JSON-RPC error for that request, so a waiting call always ends and its
  session is never torn down from inside the SDK (G7);
- responses are asked for, and must come, without content encoding: a compressed body could
  inflate far past what was counted;
- the GET stream a server may use to send its own requests is never opened: the transport
  answers 405, which the protocol allows (I2).

Each request's sizes are kept by the platform's call id (`_meta.platform_call`), for the call's
span and audit entry, which carry sizes and never content (G8)."""

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

TOO_LARGE = "tool_result_too_large"
TRANSPORT_TIMEOUT = "tool_timeout"
TRANSPORT_FAILED = "tool_unavailable"
CALL_META = "platform_call"
REQUEST_DROPPED = "server_request_dropped"
_ERROR_CODE = -32099
# The protocol's own messages (initialize, tools/list) are bounded too, by a fixed limit: the
# configured cap is a tool result's (I7).
PROTOCOL_MAX_BYTES = 1024 * 1024
# The end of one SSE event: a blank line, in any of the line endings the format allows.
_EVENT_END = re.compile(rb"\r\n\r\n|\n\n|\r\r")


@dataclass
class Sizes:
    request_bytes: int = 0
    response_bytes: int = 0
    too_large: bool = False


def _request(request: httpx.Request) -> tuple[object, str | None, str | None]:
    """The JSON-RPC id and method of a request message (None for a response or a notification
    the client sends), and the platform call id in its `_meta`."""
    try:
        body = json.loads(request.content or b"{}")
    except (ValueError, UnicodeDecodeError):
        return None, None, None
    if not isinstance(body, dict) or not isinstance(body.get("method"), str):
        return None, None, None
    params = body.get("params") if isinstance(body.get("params"), dict) else {}
    meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
    call = meta.get(CALL_META)
    return body.get("id"), call if isinstance(call, str) else None, body["method"]


def _kind(message: object, request_id: object) -> str | None:
    """`answer` for the response to `request_id`, `request` for a request the server sends (it
    has a method and an id), None for anything else (a notification, another id)."""
    if not isinstance(message, dict) or "id" not in message:
        return None
    if isinstance(message.get("method"), str):
        return "request"
    if message.get("id") == request_id and ("result" in message or "error" in message):
        return "answer"
    return None


def _event_kind(event: bytes, request_id: object) -> str | None:
    """`_kind` of the message one SSE event carries."""
    lines = event.replace(b"\r\n", b"\n").replace(b"\r", b"\n").split(b"\n")
    data = b"\n".join(line[5:].lstrip() for line in lines if line.startswith(b"data:"))
    if not data or b'"id"' not in data:
        return None
    try:
        return _kind(json.loads(data), request_id)
    except (ValueError, UnicodeDecodeError):
        return None


def _json_kind(body: bytes, request_id: object) -> str | None:
    try:
        return _kind(json.loads(body), request_id)
    except (ValueError, UnicodeDecodeError):
        return None


def _dropped() -> None:
    log.warning(REQUEST_DROPPED)


def _error_message(request_id: object, message: str) -> bytes:
    return json.dumps(
        {"jsonrpc": "2.0", "id": request_id, "error": {"code": _ERROR_CODE, "message": message}}
    ).encode("utf-8")


def _error_event(request_id: object, message: str) -> bytes:
    return b"event: message\ndata: " + _error_message(request_id, message) + b"\n\n"


class _EventStream(httpx.AsyncByteStream):
    """An SSE response, passed on one complete event at a time until the answer (see above)."""

    def __init__(
        self, response: httpx.Response, request_id: object, limit: int, deadline: float, sizes: Sizes
    ):
        self._response = response
        self._request_id = request_id
        self._limit = limit
        self._deadline = deadline
        self._sizes = sizes
        self._closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        stream = self._response.stream.__aiter__()  # type: ignore[union-attr]
        pending = bytearray()  # the event being received, never more
        scanned = 0  # bytes of `pending` already searched for an event's end
        total = 0  # every byte the server sent
        failure: str | None = None
        try:
            while True:
                try:
                    async with asyncio.timeout_at(self._deadline):
                        chunk = await stream.__anext__()
                except StopAsyncIteration:
                    failure = TRANSPORT_FAILED  # the stream ended without the answer
                    break
                except TimeoutError:
                    failure = TRANSPORT_TIMEOUT
                    break
                except httpx.HTTPError:
                    failure = TRANSPORT_FAILED
                    break
                start_total = total - len(pending)  # the stream offset of pending[0]
                total += len(chunk)
                pending += chunk
                complete: list[bytes] = []
                answered = False
                while True:
                    # Back up three bytes so an event end split across chunks is found.
                    found = _EVENT_END.search(pending, max(0, scanned - 3))
                    if found is None:
                        scanned = len(pending)
                        break
                    end = found.end()
                    if start_total + end > self._limit:
                        break
                    event = bytes(pending[:end])
                    del pending[:end]
                    start_total += end
                    scanned = 0
                    kind = _event_kind(event, self._request_id)
                    if kind == "request":
                        _dropped()  # never answered: the SDK does not see it (ADR-0246)
                        continue
                    complete.append(event)
                    if kind == "answer":
                        answered = True
                        break
                # The sizes are kept before the SDK sees the answer: the call may end at once.
                self._sizes.response_bytes = start_total if answered else total
                if complete:
                    yield b"".join(complete)
                if answered:
                    return
                if total > self._limit:
                    self._sizes.too_large = True
                    failure = TOO_LARGE
                    break
            if failure is not None:
                yield _error_event(self._request_id, failure)
        finally:
            await self.aclose()

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            await self._response.aclose()


class CappedTransport(httpx.AsyncBaseTransport):
    def __init__(
        self, max_bytes: int, timeout_s: float = 10.0, inner: httpx.AsyncBaseTransport | None = None
    ):
        self.max_bytes = max_bytes
        self.timeout_s = timeout_s
        self._inner = inner or httpx.AsyncHTTPTransport()
        self.sizes: dict[str, Sizes] = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(405, request=request)
        request.headers["accept-encoding"] = "identity"
        deadline = asyncio.get_running_loop().time() + self.timeout_s
        request_id, call, method = _request(request) if request.method == "POST" else (None, None, None)
        limit = self.max_bytes if method == "tools/call" else PROTOCOL_MAX_BYTES
        sizes = Sizes(request_bytes=len(request.content or b""))
        if call is not None:
            self.sizes[call] = sizes
        # A failure of one request is that request's error, never an exception inside the SDK's task
        # group, which would end the session every other call shares (G7).
        try:
            async with asyncio.timeout_at(deadline):
                response = await self._inner.handle_async_request(request)
        except TimeoutError:
            return self._error(request, request_id, TRANSPORT_TIMEOUT, failed=True)
        except httpx.HTTPError:
            return self._error(request, request_id, TRANSPORT_FAILED, failed=True)
        if request.method != "POST":
            return response
        encoding = response.headers.get("content-encoding", "identity").lower()
        if encoding not in ("", "identity"):
            await response.aclose()
            return self._error(request, request_id, "content_encoding_refused")
        headers = [(k, v) for k, v in response.headers.items() if k.lower() != "content-length"]
        streaming = response.headers.get("content-type", "").lower().startswith("text/event-stream")
        if streaming and request_id is not None and response.status_code == 200:
            stream = _EventStream(response, request_id, limit, deadline, sizes)
            return httpx.Response(response.status_code, headers=headers, stream=stream, request=request)
        body = bytearray()
        failure: str | None = None
        try:
            async with asyncio.timeout_at(deadline):
                async for chunk in response.stream:  # type: ignore[union-attr]
                    body += chunk
                    sizes.response_bytes = len(body)
                    if len(body) > limit:
                        sizes.too_large = True
                        break
        except TimeoutError:
            failure = TRANSPORT_TIMEOUT
        except httpx.HTTPError:
            failure = TRANSPORT_FAILED
        finally:
            await response.aclose()
        if failure is not None:
            return self._error(request, request_id, failure, failed=True)
        if sizes.too_large:
            return self._error(request, request_id, TOO_LARGE)
        if request_id is not None and _json_kind(bytes(body), request_id) == "request":
            _dropped()  # a request in place of the answer: dropped, and the answer never came
            return self._error(request, request_id, TRANSPORT_FAILED, failed=True)
        return httpx.Response(response.status_code, headers=headers, content=bytes(body), request=request)

    @staticmethod
    def _error(
        request: httpx.Request, request_id: object, message: str, *, failed: bool = False
    ) -> httpx.Response:
        if request_id is None:
            # A notification, a response the client sends, a DELETE: nothing waits for a body.
            return httpx.Response(503 if failed else 413, request=request)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=_error_message(request_id, message),
            request=request,
        )

    async def aclose(self) -> None:
        await self._inner.aclose()
