"""A report spec as it streams (option C, ADR-0406): each section leaves for the panel as soon as it is
complete, checked and verified, as the text answer leaves sentence by sentence (ADR-0039).

The model's tokens queue; a task feeds them to `SectionReader`. The head (title, subtitle, period, language)
and then each completed section pass the OUTPUT redact policies and DATA on their visible strings, joined as
fields (app/guardrails/fields.py) so a redaction lands in the string it matched; the section is verified
against its cited passages (app/artifacts/verify.py) and rendered (app/artifacts/render.py), and its HTML
streams as `artifact_delta`, the first preceded by `artifact_start` and the page's frame and header.

Buffered mode (an OUTPUT policy that can block, rewrite or redact applies, D3 of phase 03): nothing leaves
while the model writes; the whole spec is checked, then streamed in parts.

`close()` parses the whole answer as a spec (a `SpecError` makes the writer try again once), handles any
section the stream did not complete, and runs the whole-spec pass, as the text answer's guard nodes do
(ADR-0111): OUTPUT with every action and DATA on all visible strings together. A block ends the turn; a
rewrite cannot apply to a spec and ends it as a block; a redaction changes the strings."""

import asyncio
from typing import Any

from opentelemetry import trace

from app.artifacts.config import ReportConfig
from app.artifacts.render import PageRenderer
from app.artifacts.spec import SECTION, Head, ReportSpec, SectionReader, SpecError, parse, section
from app.artifacts.verify import Verifier
from app.errors import TurnError
from app.graph.deps import TurnContext
from app.graph.guarding import blocked, guard_context
from app.graph.state import AgentState
from app.graph.streaming import SENTENCE_OUTPUT_ACTIONS
from app.guardrails.checkpoints import Checkpoint
from app.guardrails.fields import join_fields, split_fields
from app.guardrails.schema import DataPayload, GuardContext, GuardEventRecord
from app.resilience import deadline

_END = object()
# A buffered page streams in parts of this size once it passed.
BUFFERED_PART_CHARS = 8000
# Keys whose values are the spec's structure, never text a person reads.
_STRUCTURE = frozenset({"kind", "type", "tone", "sources", "language"})


def strings(value: Any, path: tuple = ()) -> list[tuple[tuple, str]]:
    """(path, text) of every string a person reads in a spec, a head or a section (dumped to plain data)."""
    found: list[tuple[tuple, str]] = []
    if isinstance(value, str):
        if value.strip():
            found.append((path, value))
    elif isinstance(value, dict):
        for key, item in value.items():
            if key not in _STRUCTURE:
                found += strings(item, (*path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found += strings(item, (*path, index))
    return found


def _put(value: Any, path: tuple, text: str) -> None:
    for key in path[:-1]:
        value = value[key]
    value[path[-1]] = text


def visible_text(spec: ReportSpec | None) -> str:
    """The spec's visible text, one string per line: what the script and source-name checks read."""
    return "\n".join(text for _, text in strings(spec.model_dump(mode="json"))) if spec else ""


class SpecStream:
    def __init__(
        self,
        ctx: TurnContext,
        state: AgentState,
        config: ReportConfig,
        renderer: PageRenderer,
        verifier: Verifier,
        *,
        report_id: str,
        version: int,
        buffered: bool,
    ):
        self._ctx = ctx
        self._config = config
        self._context = guard_context(state, ctx)
        self._output_context = guard_context(state, ctx, actions=SENTENCE_OUTPUT_ACTIONS)
        self._renderer = renderer
        self._verifier = verifier
        self._buffered = buffered
        self._id = report_id
        self._version = version
        self._reader = SectionReader()
        self.head: Head | None = None
        self._waiting: list[dict] = []  # sections complete before the head was known
        self.sections: list[Any] = []  # checked and verified sections, in order (None: dropped)
        self._seen = 0  # sections the reader completed
        self.streamed: list[str] = []
        self._started = False
        self._matched = False
        self._fed = 0
        self.truncated = False
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task = asyncio.create_task(self._run())

    # ------------------------------------------------------------------ the model's side
    def feed(self, delta: str) -> None:
        """The model gateway's on_token callback (synchronous)."""
        self._queue.put_nowait(delta)

    async def close(self) -> ReportSpec:
        """The checked and verified spec; raises SpecError (not a spec) or the TurnError of a block."""
        self._queue.put_nowait(_END)
        try:
            await asyncio.wait([self._task])
        except asyncio.CancelledError:
            await deadline.drain(self._task, name="report:stream")
            raise
        self._task.result()
        spec = parse(self._reader.text)
        if self.head is None:
            self.head = await self._head(Head(title=spec.title, subtitle=spec.subtitle, period=spec.period,
                                              language=spec.language))
        # A section the stream did not see whole (the answer was cut, or one failed its schema mid-stream).
        for raw in spec.model_dump(mode="json")["sections"][len(self.sections):]:
            await self._section(raw, emit=not self._buffered)
        kept = [s for s in self.sections if s is not None]
        if not kept:
            raise SpecError("no section survived")
        checked = await self._whole(ReportSpec(
            title=self.head.title, subtitle=self.head.subtitle, period=self.head.period, language=self.head.language,
            sections=kept,
        ))
        if not self._matched:
            self._ctx.report_guard(
                [GuardEventRecord(checkpoint=Checkpoint.DATA, action="allow", target="answer",
                                  detail="report spec, no policy matched")]
            )
        return checked

    async def abort(self) -> None:
        await deadline.drain(self._task, name="report:stream")

    # ------------------------------------------------------------------ the queue's side
    async def _run(self) -> None:
        limit = self._config.max_model_chars
        while (item := await self._queue.get()) is not _END:
            if self._fed >= limit:
                self.truncated = True
                continue
            item = item[: limit - self._fed]
            self._fed += len(item)
            self._reader.feed(item)
            for found in self._reader.take():
                if isinstance(found, Head):
                    self.head = await self._head(found)
                    for raw in self._waiting:
                        await self._section(raw, emit=not self._buffered)
                    self._waiting = []
                elif self.head is None:
                    self._waiting.append(found)
                else:
                    await self._section(found, emit=not self._buffered)

    async def _head(self, head: Head) -> Head:
        data = head.model_dump(mode="json")
        await self._checked(data)
        return Head.model_validate(data)

    async def _section(self, raw: dict, *, emit: bool) -> None:
        self._seen += 1
        parsed = section(raw)
        if parsed is None:
            self.sections.append(None)
            return
        data = parsed.model_dump(mode="json")
        await self._checked(data)
        verified = self._verifier.element(SECTION.validate_python(data))
        self.sections.append(verified)
        if emit and verified is not None:
            self._delta(self._renderer.section(verified))

    async def _checked(self, data: dict) -> None:
        """OUTPUT (sentence actions) and DATA on a part's strings, written back in place."""
        found = strings(data)
        if not found:
            return
        texts = [text for _, text in found]
        joined = join_fields("", texts)
        output = await self._check(Checkpoint.OUTPUT, {"answer": joined, "citations": []}, self._output_context, "OUTPUT")
        checked = await self._check(
            Checkpoint.DATA, DataPayload(text=output.payload["answer"], target="answer"), self._context, "DATA"
        )
        _, back = split_fields(checked.payload.text, "", texts)
        for (path, _), text in zip(found, back, strict=True):
            _put(data, path, text)

    async def _whole(self, spec: ReportSpec) -> ReportSpec:
        """OUTPUT with every action and DATA on the whole spec's strings (ADR-0111's pass)."""
        data = spec.model_dump(mode="json")
        found = strings(data)
        if not found:
            return spec
        texts = [text for _, text in found]
        joined = join_fields("", texts)
        output = await self._check(Checkpoint.OUTPUT, {"answer": joined, "citations": []}, self._context, "OUTPUT")
        if output.action == "rewrite":
            raise blocked("output_blocked", output)
        checked = await self._check(
            Checkpoint.DATA, DataPayload(text=output.payload["answer"], target="answer"), self._context, "DATA"
        )
        _, back = split_fields(checked.payload.text, "", texts)
        if back != texts:
            trace.get_current_span().set_attribute("report.final_pass", "redacted")
            for (path, _), text in zip(found, back, strict=True):
                _put(data, path, text)
        return ReportSpec.model_validate(data)

    def _delta(self, html: str) -> None:
        if not html:
            return
        if not self._started:
            self._started = True
            self._ctx.emit("artifact_start", {"id": self._id, "title": self.head.title if self.head else "",
                                              "version": self._version})
            html = self._renderer.start(self.head) + html  # type: ignore[arg-type]
        self.streamed.append(html)
        self._ctx.emit("artifact_delta", {"id": self._id, "text": html})

    def stream_page(self, page: str) -> None:
        """Buffered mode: the checked page leaves in parts once it passed."""
        if not self._started:
            self._started = True
            self._ctx.emit("artifact_start", {"id": self._id, "title": self.head.title if self.head else "",
                                              "version": self._version})
        for at in range(0, len(page), BUFFERED_PART_CHARS):
            self.streamed.append(page[at : at + BUFFERED_PART_CHARS])
            self._ctx.emit("artifact_delta", {"id": self._id, "text": page[at : at + BUFFERED_PART_CHARS]})

    async def _check(self, checkpoint: Checkpoint, payload, context: GuardContext, label: str):
        ctx = self._ctx
        outcome = await ctx.services.guard.evaluate(checkpoint, payload, principal=ctx.principal, context=context)
        matched = [e for e in outcome.events if e.policy_id]
        if matched:
            self._matched = self._matched or any(not e.audit_only for e in matched)
            ctx.report_guard(matched)
        if outcome.unavailable:
            raise TurnError("guard_unavailable", retryable=True, detail=f"{label}: {outcome.unavailable}")
        if outcome.blocked:
            raise blocked("output_blocked" if checkpoint == Checkpoint.OUTPUT else "data_blocked", outcome)
        return outcome


__all__ = ["SpecError", "SpecStream", "strings", "visible_text"]
