"""The answer leaves the agent sentence by sentence, each through DATA first
(ADR-0039 full fix, D3, I5).

`SentenceBuffer` cuts the token stream into sentences: after a terminator
(. ? ؟ ! ؛ by default) followed by whitespace, at a newline, or at `flush_chars`
characters. Waiting for the whitespace keeps "3.5", "x.y@example.org" and
"055.123.4567" whole. Neither a sentence cut nor a forced one ever falls inside a
digit run joined by spaces, hyphens, dots, commas or the Arabic separators ٫ ٬ ،
("050 - 123 - 4567", "٠٥٠ ١٢٣ ٤٥٦٧"): where the run may go on past the end of the
buffer, the cut waits for the next chunk (ADR-0111). A newline still always cuts;
the whole-answer pass below catches what it splits.

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


# What may join the digits of one number: whitespace (not a newline), hyphens and dashes,
# dot, comma, slash, and the Arabic decimal and thousands separators and comma.
DIGIT_JOINERS = frozenset(" \t\u00a0-\u2010\u2011\u2012\u2013\u2014.,/\u066b\u066c\u060c")


def splits_digits(text: str, cut: int) -> bool | None:
    """Whether cutting `text` before index `cut` splits a digit run joined only by
    DIGIT_JOINERS: True or False, or None while the run reaches the end of the buffer,
    so the next chunk decides."""
    before = cut - 1
    while before >= 0 and text[before] in DIGIT_JOINERS:
        before -= 1
    if before < 0 or not text[before].isdigit():
        return False
    after = cut
    while after < len(text) and text[after] in DIGIT_JOINERS:
        after += 1
    if after == len(text):
        return None
    return text[after].isdigit()


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
                split = splits_digits(text, i + 2)
                if split is None:
                    return None  # "… 050. " at the end: the next chunk decides
                if not split:
                    return i + 2
        if len(text) < self._flush:
            return None
        # A forced cut: the last whitespace within the limit that splits no digit run.
        for i in range(self._flush - 1, 0, -1):
            if text[i].isspace():
                split = splits_digits(text, i + 1)
                if split is None:
                    return None
                if not split:
                    return i + 1
        # No whitespace to cut at: the last position within the limit outside a digit run.
        for cut in range(self._flush, 0, -1):
            split = splits_digits(text, cut)
            if split is None:
                return None
            if not split:
                return cut
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
            # An audit-only record (a clear judge call's usage) is not a match: the client's
            # `allow` record must not depend on whether a verdict was cached (ADR-0118).
            self._matched = self._matched or any(not e.audit_only for e in matched)
            ctx.report_guard(matched)
        if outcome.unavailable:
            raise TurnError("guard_unavailable", retryable=True, detail=f"{label}: {outcome.unavailable}")
        if outcome.blocked:
            raise blocked("data_blocked", outcome)
        return outcome
