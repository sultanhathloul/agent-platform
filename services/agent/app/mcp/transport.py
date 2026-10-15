"""The HTTP layer under the MCP SDK's Streamable HTTP client (I2, I7, ADR-0203, ADR-0208).

The SDK reads a response body whole before it parses it, and a body it fails to read leaves the
call waiting for its timeout. This transport, which the SDK's httpx client sends every request
through, reads each POST response itself, counting bytes as they arrive:

- past `max_bytes` it stops reading, closes the connection and hands the SDK, in place of the
  body, a JSON-RPC error for that request with the message `tool_result_too_large`; nothing
  beyond the cap is read or parsed;
- an SSE response is read event by event until the event that answers the request, so a server
  that keeps the stream open costs nothing more;
- responses are asked for, and must come, without content encoding: a compressed body could
  inflate far past what was counted;
- the GET stream a server may use to send its own requests (sampling, elicitation, roots) is
  never opened: the transport answers 405, which the protocol allows, so a server cannot reach
  back into the platform (I2).

Each request's sizes are kept by the platform's call id (`_meta.platform_call`), for the call's
span and audit entry, which carry sizes and never content (G8)."""

import json
from dataclasses import dataclass

import httpx

TOO_LARGE = "tool_result_too_large"
TRANSPORT_TIMEOUT = "tool_timeout"
TRANSPORT_FAILED = "tool_unavailable"
CALL_META = "platform_call"
_TOO_LARGE_CODE = -32099
# The protocol's own messages (initialize, tools/list) are bounded too, by a fixed limit: the
# configured cap is a tool result's (I7).
PROTOCOL_MAX_BYTES = 1024 * 1024


@dataclass
class Sizes:
    request_bytes: int = 0
    response_bytes: int = 0
    too_large: bool = False


def _request(request: httpx.Request) -> tuple[object, str | None, str | None]:
    """The JSON-RPC id and method of the request, and the platform call id in its `_meta`."""
    try:
        body = json.loads(request.content or b"{}")
    except (ValueError, UnicodeDecodeError):
        return None, None, None
    if not isinstance(body, dict):
        return None, None, None
    params = body.get("params") if isinstance(body.get("params"), dict) else {}
    meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
    call = meta.get(CALL_META)
    method = body.get("method")
    return (
        body.get("id"),
        call if isinstance(call, str) else None,
        method if isinstance(method, str) else None,
    )


def _answers(event: bytes, request_id: object) -> bool:
    """Whether one SSE event carries the response to `request_id`."""
    data = b"\n".join(line[5:].lstrip() for line in event.splitlines() if line.startswith(b"data:"))
    if not data:
        return False
    try:
        message = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return False
    return (
        isinstance(message, dict)
        and message.get("id") == request_id
        and ("result" in message or "error" in message)
    )


class CappedTransport(httpx.AsyncBaseTransport):
    def __init__(self, max_bytes: int, inner: httpx.AsyncBaseTransport | None = None):
        self.max_bytes = max_bytes
        self._inner = inner or httpx.AsyncHTTPTransport()
        self.sizes: dict[str, Sizes] = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(405, request=request)
        request.headers["accept-encoding"] = "identity"
        request_id, call, method = _request(request) if request.method == "POST" else (None, None, None)
        limit = self.max_bytes if method == "tools/call" else PROTOCOL_MAX_BYTES
        sizes = Sizes(request_bytes=len(request.content or b""))
        if call is not None:
            self.sizes[call] = sizes
        # A failure of one request is that request's error, never an exception inside the SDK's task
        # group, which would end the session every other call shares (G7).
        try:
            response = await self._inner.handle_async_request(request)
        except httpx.TimeoutException:
            return self._error(request, request_id, TRANSPORT_TIMEOUT, failed=True)
        except httpx.HTTPError:
            return self._error(request, request_id, TRANSPORT_FAILED, failed=True)
        if request.method != "POST":
            return response
        encoding = response.headers.get("content-encoding", "identity").lower()
        if encoding not in ("", "identity"):
            await response.aclose()
            return self._error(request, request_id, "content_encoding_refused")
        streaming = response.headers.get("content-type", "").lower().startswith("text/event-stream")
        body = bytearray()
        failure: str | None = None
        try:
            async for chunk in response.stream:  # type: ignore[union-attr]
                body += chunk
                sizes.response_bytes = len(body)
                if streaming and request_id is not None:
                    end = self._answer_end(body, request_id)
                    if end is not None and end <= limit:
                        del body[end:]  # the events after the answer are never read
                        sizes.response_bytes = end
                        break
                if len(body) > limit:
                    sizes.too_large = True
                    break
        except httpx.TimeoutException:
            failure = TRANSPORT_TIMEOUT
        except httpx.HTTPError:
            failure = TRANSPORT_FAILED
        finally:
            await response.aclose()
        if failure is not None:
            return self._error(request, request_id, failure, failed=True)
        if sizes.too_large:
            return self._error(request, request_id, TOO_LARGE)
        headers = [(k, v) for k, v in response.headers.items() if k.lower() not in ("content-length",)]
        return httpx.Response(response.status_code, headers=headers, content=bytes(body), request=request)

    @staticmethod
    def _answer_end(body: bytearray, request_id: object) -> int | None:
        """The offset just past the SSE event that answers `request_id`, when it has arrived."""
        position = 0
        data = bytes(body)
        while True:
            boundary = data.find(b"\n\n", position)
            crlf = data.find(b"\r\n\r\n", position)
            if crlf != -1 and (boundary == -1 or crlf < boundary):
                boundary, width = crlf, 4
            else:
                width = 2
            if boundary == -1:
                return None
            if _answers(data[position:boundary].replace(b"\r\n", b"\n"), request_id):
                return boundary + width
            position = boundary + width

    @staticmethod
    def _error(
        request: httpx.Request, request_id: object, message: str, *, failed: bool = False
    ) -> httpx.Response:
        if request_id is None:
            # A notification, a DELETE or a GET: nothing waits for a body.
            return httpx.Response(503 if failed else 413, request=request)
        payload = {"jsonrpc": "2.0", "id": request_id, "error": {"code": _TOO_LARGE_CODE, "message": message}}
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=json.dumps(payload).encode("utf-8"),
            request=request,
        )

    async def aclose(self) -> None:
        await self._inner.aclose()
