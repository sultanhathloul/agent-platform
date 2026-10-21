"""A slow OpenAI-compatible model provider behind LiteLLM (timeout budget PR, scopes D and E; I2,
I4; ADR-0255, ADR-0257). infra/litellm/config.mock.yaml routes its test-only `slow-*` aliases here,
to http://agent-tests:8765 (`make test-agent` runs the tests with --use-aliases, so the test
container answers to its service name). It runs uvicorn in a thread of its own, with its own event
loop, so its work never shows in the agent's event-loop lag that a test measures.

What it does is chosen by the upstream model name LiteLLM sends (the alias's `openai/<name>`):

- `hung`: never answers, not even headers, until the client goes away;
- `trickle`: a stream that sends one token every TRICKLE_S and never ends;
- `flood`: a stream of FLOOD_CHUNKS one-character tokens as fast as it can, then the end;
- `ok`: a short streamed answer at once;
- `drop`: one token, then the connection is dropped (an error after the first token);
- `status429`, `status500`: that status at once, its body `status_body_bytes` long when set (the
  PR #12 review, decision 3: a 2 MiB error body).

`ok_pace_s`, when set, spaces the words of the `ok` answer by that many seconds: an answer that
takes a known time and then ends (the PR #12 review, decision 2). Both are reset by `reset`.

Embeddings (`/v1/embeddings`) and rerank (`/rerank`, Infinity's shape) answer `hung`, `ok` and
the status models the same way; `trickle` sends their JSON one byte every TRICKLE_S, and `flood`
sends an answer of FLOOD_BYTES in tiny pieces. A request is open from the moment it arrives until
its response is complete or its client has gone: `open` counts them now, `peak` at worst since
`reset`, `served` and `abandoned` how they ended, per model name."""

import asyncio
import json
import threading
import time
from collections import Counter

import uvicorn

PORT = 8765
TRICKLE_S = 2.0
FLOOD_CHUNKS = 20000
FLOOD_BYTES = 2 * 1024 * 1024
OK_TEXT = "هذه إجابة من المزود البطيء."


def _chunk(text: str | None, *, finish: str | None = None, usage: bool = False) -> bytes:
    body: dict = {
        "id": "chatcmpl-slow",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "slow",
        "choices": [{"index": 0, "delta": {"content": text} if text else {}, "finish_reason": finish}],
    }
    if usage:
        body["choices"] = []
        body["usage"] = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    return b"data: " + json.dumps(body, ensure_ascii=False).encode() + b"\n\n"


class SlowUpstream:
    def __init__(self, port: int = PORT) -> None:
        self.port = port
        self.open: Counter[str] = Counter()
        self.peak: Counter[str] = Counter()
        self.served: Counter[str] = Counter()
        self.abandoned: Counter[str] = Counter()
        self.arrived: Counter[str] = Counter()
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self.ok_pace_s = 0.0
        self.status_body_bytes = 0

    def reset(self) -> None:
        for counter in (self.peak, self.served, self.abandoned, self.arrived):
            counter.clear()
        self.peak.update(self.open)
        self.ok_pace_s = 0.0
        self.status_body_bytes = 0

    def open_total(self) -> int:
        return sum(self.open.values())

    async def _body(self, receive) -> bytes:
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                return body

    async def app(self, scope, receive, send) -> None:
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["path"] == "/health":
            await self._json(send, 200, {"status": "ok"})
            return
        request = json.loads(await self._body(receive) or b"{}")
        model = str(request.get("model", "")).split("/")[-1]
        self.arrived[model] += 1
        self.open[model] += 1
        self.peak[model] = max(self.peak[model], self.open[model])
        disconnected = asyncio.ensure_future(receive())  # http.disconnect when the client goes away
        try:
            completed = await self._answer(scope["path"], model, request, send, disconnected)
            (self.served if completed else self.abandoned)[model] += 1
        finally:
            disconnected.cancel()
            self.open[model] -= 1

    async def _answer(self, path: str, model: str, request: dict, send, disconnected) -> bool:
        """True when the response was sent whole; False when the client went away first."""
        if model == "hung":
            await disconnected
            return False
        if model.startswith("status"):
            message = "slow upstream" + " " * self.status_body_bytes
            await self._json(send, int(model.removeprefix("status")), {"error": {"message": message}})
            return True
        if path.endswith("/embeddings") or path.endswith("/rerank"):
            if path.endswith("/embeddings"):
                inputs = request.get("input") or [""]
                # A flood is FLOOD_BYTES in all, however many inputs: the fixture's own JSON work runs
                # in this process, so it is kept to that size (the PR #12 review, decision 10).
                vector = [0.1, 0.2, 0.3, 0.4] * (FLOOD_BYTES // (20 * len(inputs)) if model == "flood" else 1)
                data = [{"object": "embedding", "index": i, "embedding": vector} for i in range(len(inputs))]
                payload = {
                    "object": "list",
                    "data": data,
                    "model": model,
                    "usage": {"prompt_tokens": 1, "total_tokens": 1},
                }
            else:
                documents = request.get("documents") or []
                results = [{"index": i, "relevance_score": 1.0 / (i + 1)} for i in range(len(documents))]
                payload = {"results": results, "model": model, "usage": {"total_tokens": 1}}
                if model == "flood":
                    payload["padding"] = " " * FLOOD_BYTES
            body = json.dumps(payload).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            if model == "trickle":
                for i in range(len(body)):
                    await send({"type": "http.response.body", "body": body[i : i + 1], "more_body": True})
                    done, _ = await asyncio.wait([disconnected], timeout=TRICKLE_S)
                    if done:
                        return False
            elif model == "flood":
                for i in range(0, len(body), 64):
                    if disconnected.done():
                        return False
                    await send({"type": "http.response.body", "body": body[i : i + 64], "more_body": True})
                    if i % (64 * 500) == 0:
                        await asyncio.sleep(0)
            else:
                await send({"type": "http.response.body", "body": body, "more_body": True})
            await send({"type": "http.response.body", "body": b""})
            return True
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )
        if model == "trickle":
            while True:
                await send({"type": "http.response.body", "body": _chunk("ك"), "more_body": True})
                done, _ = await asyncio.wait([disconnected], timeout=TRICKLE_S)
                if done:
                    return False
        if model == "drop":
            await send({"type": "http.response.body", "body": _chunk("ك"), "more_body": True})
            await asyncio.sleep(0.5)  # the token reaches the agent first
            raise RuntimeError("dropped")  # uvicorn closes the connection mid-response
        if model == "flood":
            for i in range(FLOOD_CHUNKS):
                if disconnected.done():
                    return False
                await send({"type": "http.response.body", "body": _chunk("ب"), "more_body": True})
                if i % 500 == 0:
                    await asyncio.sleep(0)
        else:  # ok
            for word in OK_TEXT.split(" "):
                await send({"type": "http.response.body", "body": _chunk(word + " "), "more_body": True})
                if self.ok_pace_s:
                    done, _ = await asyncio.wait([disconnected], timeout=self.ok_pace_s)
                    if done:
                        return False
        tail = _chunk(None, finish="stop") + _chunk(None, usage=True) + b"data: [DONE]\n\n"
        await send({"type": "http.response.body", "body": tail})
        return True

    async def _json(self, send, status: int, payload: dict) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": json.dumps(payload).encode()})

    def start(self) -> None:
        config = uvicorn.Config(
            self.app, host="0.0.0.0", port=self.port, log_level="warning", lifespan="off", interface="asgi3"
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, name="slow-upstream", daemon=True)
        self._thread.start()
        for _ in range(200):
            if self._server.started:
                return
            threading.Event().wait(0.05)
        raise RuntimeError("slow upstream did not start")

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
            self._server.force_exit = True
        if self._thread is not None:
            self._thread.join(10)

    async def settled(self, model: str, within_s: float) -> float:
        """Seconds until no `model` request is open here; raises past `within_s`."""
        started = time.monotonic()
        while self.open[model]:
            if time.monotonic() - started > within_s:
                raise AssertionError(f"{self.open[model]} {model} request(s) still open after {within_s} s")
            await asyncio.sleep(0.05)
        return time.monotonic() - started
