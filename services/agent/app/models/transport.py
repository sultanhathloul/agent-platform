"""A byte cap on every answer from LiteLLM, whatever its status (the PR #12 review, decision 3,
ADR-0266; CLAUDE.md rule 12d).

The OpenAI SDK reads an error answer whole and parses it as JSON before raising (a 500 or a 429
body of any size, on the event loop). This httpx transport sits under the SDK's client and counts
the answer's bytes as they are read, so the read stops past the cap before the SDK or anything else
parses it:

- an error answer (any status but 2xx), and a 2xx answer that is not a stream: at most `body_cap`
  bytes (1 MiB), refused at once when its Content-Length says more;
- a streamed 2xx answer (text/event-stream): at most `event_cap` bytes between two newlines, so no
  single event is buffered past it; its length as a whole is the model's output, bounded by the
  call's deadline and max_tokens;
- `cap_success=False` (embeddings): a 2xx answer is left to its caller's own cap per input text,
  read before parsing (app/models/embeddings.py, read_capped).

Every request asks for an uncompressed answer (Accept-Encoding: identity), so the bytes counted are
the bytes parsed. Past a cap the read raises AnswerTooLarge, which the model and embedding gateways
report as ModelUnavailable. Built on httpx2, the HTTP client the OpenAI SDK uses."""

import httpx2 as httpx

ANSWER_CAP_BYTES = 1024 * 1024
EVENT_CAP_BYTES = 1024 * 1024
# The SDK's own connection limits for the transport it would otherwise build (openai._constants).
_LIMITS = httpx.Limits(max_connections=1000, max_keepalive_connections=100)


class AnswerTooLarge(Exception):
    """An answer from LiteLLM past its byte cap; nothing of it was parsed."""


class _Capped(httpx.AsyncByteStream):
    def __init__(self, inner: httpx.AsyncByteStream, *, total: int | None, event: int | None, declared: int):
        self._inner = inner
        self._total = total
        self._event = event
        self._declared = declared

    async def __aiter__(self):
        if self._total is not None and self._declared > self._total:
            await self._refuse(f"answer of {self._declared} bytes declared, cap {self._total}")
        read = 0
        since_newline = 0
        async for chunk in self._inner:
            read += len(chunk)
            if self._total is not None and read > self._total:
                await self._refuse(f"answer over its {self._total} byte cap")
            if self._event is not None:
                newline = chunk.rfind(b"\n")
                since_newline = since_newline + len(chunk) if newline < 0 else len(chunk) - newline - 1
                if since_newline > self._event:
                    await self._refuse(f"a streamed event over its {self._event} byte cap")
            yield chunk

    async def _refuse(self, why: str) -> None:
        """Closes the connection, which aborts the answer at LiteLLM, and raises."""
        await self._inner.aclose()
        raise AnswerTooLarge(why)

    async def aclose(self) -> None:
        await self._inner.aclose()


class CappedTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        inner: httpx.AsyncBaseTransport | None = None,
        *,
        body_cap: int = ANSWER_CAP_BYTES,
        event_cap: int = EVENT_CAP_BYTES,
        cap_success: bool = True,
    ):
        self._inner = inner or httpx.AsyncHTTPTransport(limits=_LIMITS)
        self._body_cap = body_cap
        self._event_cap = event_cap
        self._cap_success = cap_success

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        request.headers["Accept-Encoding"] = "identity"
        response = await self._inner.handle_async_request(request)
        ok = 200 <= response.status_code < 300
        streamed = ok and response.headers.get("content-type", "").startswith("text/event-stream")
        total = None if streamed or (ok and not self._cap_success) else self._body_cap
        try:
            declared = int(response.headers.get("content-length", "-1"))
        except ValueError:
            declared = -1
        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            stream=_Capped(
                response.stream,
                total=total,
                event=self._event_cap if streamed else None,
                declared=declared,
            ),
            extensions=response.extensions,
            request=request,
        )

    async def aclose(self) -> None:
        await self._inner.aclose()
