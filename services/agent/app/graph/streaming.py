"""The answer leaves the agent sentence by sentence, each through DATA first
(ADR-0039 full fix, D3, I5).

`SentenceBuffer` cuts the token stream into sentences: after a terminator
(. ? ؟ ! ؛ by default) followed by whitespace, at a newline, or at `flush_chars`
characters. Waiting for the whitespace keeps "3.5", "x.y@example.org" and
"055.123.4567" whole; a forced cut goes to the last whitespace that is not between
two digits, so a number written in groups is not split across two checks.

`DataStream` is the answer node's token callback while streaming: tokens queue,
and a task releases each complete sentence after the OUTPUT redact policies and
then DATA (target `answer`) have run on it (ADR-0106), so a redaction happens before
the text leaves the agent. Redaction events are
reported per sentence; when nothing matched, one `allow` record stands for the
whole answer."""

import asyncio
import contextlib
from collections.abc import Sequence

from app.errors import TurnError
from app.graph.deps import TurnContext
from app.graph.guarding import blocked, guard_context
from app.graph.state import AgentState
from app.guardrails.checkpoints import Checkpoint
from app.guardrails.schema import DataPayload, GuardContext, GuardEventRecord

# While streaming, OUTPUT runs per sentence for its redact policies only (ADR-0106).
SENTENCE_OUTPUT_ACTIONS = frozenset({"redact"})


class SentenceBuffer:
    def __init__(self, terminators: Sequence[str], flush_chars: int):
        self._terminators = frozenset(terminators)
        self._flush = flush_chars
        self._text = ""

    def feed(self, text: str) -> list[str]:
        self._text += text
        sentences = []
        while (cut := self._boundary()) is not None:
            sentences.append(self._text[:cut])
            self._text = self._text[cut:]
        return sentences

    def flush(self) -> str:
        rest, self._text = self._text, ""
        return rest

    def _boundary(self) -> int | None:
        text = self._text
        for i, char in enumerate(text):
            if char == "\n":
                return i + 1
            if char in self._terminators and i + 1 < len(text) and text[i + 1].isspace():
                return i + 2
        if len(text) < self._flush:
            return None
        for i in range(self._flush - 1, 0, -1):
            between_digits = text[i - 1].isdigit() and i + 1 < len(text) and text[i + 1].isdigit()
            if text[i].isspace() and not between_digits:
                return i + 1
        return self._flush


def split_sentences(text: str, terminators: Sequence[str], flush_chars: int) -> list[str]:
    buffer = SentenceBuffer(terminators, flush_chars)
    parts = buffer.feed(text)
    rest = buffer.flush()
    return [*parts, rest] if rest else parts


_END = object()


class DataStream:
    def __init__(self, ctx: TurnContext, state: AgentState):
        config = ctx.services.guard_config.streaming
        self._ctx = ctx
        self._context = guard_context(state, ctx)
        self._output_context = guard_context(state, ctx, actions=SENTENCE_OUTPUT_ACTIONS)
        self._buffer = SentenceBuffer(config.terminators, config.flush_chars)
        self._queue: asyncio.Queue = asyncio.Queue()
        self._released: list[str] = []
        self._matched = False
        self._sentences = 0
        self._task = asyncio.create_task(self._run())

    def feed(self, delta: str) -> None:
        """The model gateway's on_token callback (synchronous)."""
        self._queue.put_nowait(delta)

    @property
    def released(self) -> str:
        return "".join(self._released)

    async def close(self) -> str:
        """Releases what is left and returns the released text; raises the TurnError of a
        DATA block or an unavailable policy."""
        self._queue.put_nowait(_END)
        await self._task
        if not self._matched:
            self._ctx.report_guard(
                [
                    GuardEventRecord(
                        checkpoint=Checkpoint.DATA,
                        action="allow",
                        target="answer",
                        detail=f"{self._sentences} sentence(s), no policy matched",
                    )
                ]
            )
        return self.released

    async def abort(self) -> None:
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._task

    async def _run(self) -> None:
        while (item := await self._queue.get()) is not _END:
            for sentence in self._buffer.feed(item):
                await self._release(sentence)
        if rest := self._buffer.flush():
            await self._release(rest)

    async def _release(self, sentence: str) -> None:
        self._sentences += 1
        output = await self._check(
            Checkpoint.OUTPUT, {"answer": sentence, "citations": []}, self._output_context, "OUTPUT"
        )
        sentence = output.payload["answer"]
        outcome = await self._check(
            Checkpoint.DATA, DataPayload(text=sentence, target="answer"), self._context, "DATA"
        )
        text = outcome.payload.text
        if text:
            self._ctx.emit("token", {"text": text})
            self._released.append(text)

    async def _check(self, checkpoint: Checkpoint, payload, context: GuardContext, label: str):
        ctx = self._ctx
        outcome = await ctx.services.guard.evaluate(
            checkpoint, payload, principal=ctx.principal, context=context
        )
        matched = [e for e in outcome.events if e.policy_id]
        if matched:
            self._matched = True
            ctx.report_guard(matched)
        if outcome.unavailable:
            raise TurnError("guard_unavailable", retryable=True, detail=f"{label}: {outcome.unavailable}")
        if outcome.blocked:
            raise blocked("data_blocked", outcome)
        return outcome
