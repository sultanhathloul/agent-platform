"""The report's page leaves the agent segment by segment, each through the guards first (reports PR, I1,
I4, ADR-0383), as the text answer leaves sentence by sentence (app/graph/streaming.py, ADR-0039).

The model's tokens queue; a task feeds them to the sanitizer (app/artifacts/html.py) and gathers its
pieces into a segment. A segment is released once its visible text reaches `stream.flush_chars`, or it
holds the report-data block: the OUTPUT redact policies and DATA run on its visible texts (text,
the attribute values a person reads, the data block's labels), joined as fields (app/guardrails/fields.py)
so a redaction lands in the field it matched, and then the segment is rendered with its figure marks and
streamed as `artifact_delta`. The first release is preceded by `artifact_start`.

Buffered mode (an OUTPUT policy that can block, rewrite or redact applies, D3 of phase 03): nothing is
released while the model writes; the whole page is checked once and then streamed.

`close()` runs the whole-page pass, as the text answer's guard nodes do (ADR-0111): OUTPUT with every
action and DATA on all the visible text together. A block ends the turn; a rewrite cannot apply to a page
and ends it as a block; a redaction changes the pieces. The page the writer stores is rendered again from
the checked pieces, so it is what the guards passed even where a match spanned two segments."""

import asyncio
from dataclasses import dataclass, field

from opentelemetry import trace

from app.artifacts.config import ReportConfig
from app.artifacts.document import Renderer, data_strings, normalize_data, set_data_string
from app.artifacts.figures import FigureLabels
from app.artifacts.html import VISIBLE_ATTRIBUTES, Data, Markup, Piece, Sanitizer, Text, start_tag
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
# A segment of markup with little text still leaves once it is this long, so a table streams.
MAX_PENDING_MARKUP = 8000
# A buffered page streams in parts of this size once it passed.
BUFFERED_PART_CHARS = 8000


@dataclass
class _Fields:
    """The visible texts of some pieces and where each one goes back."""

    texts: list[str] = field(default_factory=list)
    places: list[tuple] = field(default_factory=list)


def _fields(pieces: list[Piece]) -> _Fields:
    found = _Fields()
    for index, piece in enumerate(pieces):
        if isinstance(piece, Text) and piece.text.strip():
            found.texts.append(piece.text)
            found.places.append(("text", index))
        elif isinstance(piece, Markup) and not piece.end:
            for at, (name, value) in enumerate(piece.attrs):
                if name in VISIBLE_ATTRIBUTES and value.strip():
                    found.texts.append(value)
                    found.places.append(("attr", index, at))
        elif isinstance(piece, Data):
            if piece.parsed is None:
                piece.parsed = normalize_data(piece.raw)
            for path, text in data_strings(piece.parsed):
                if text.strip():
                    found.texts.append(text)
                    found.places.append(("data", index, path))
    return found


def _apply(pieces: list[Piece], fields: _Fields, checked: list[str]) -> None:
    for place, text in zip(fields.places, checked, strict=True):
        piece = pieces[place[1]]
        if place[0] == "text":
            piece.text = text  # type: ignore[union-attr]
        elif place[0] == "attr":
            name, _ = piece.attrs[place[2]]  # type: ignore[union-attr]
            piece.attrs[place[2]] = (name, text)  # type: ignore[union-attr]
            piece.html = start_tag(piece.tag, piece.attrs)  # type: ignore[union-attr]
        else:
            set_data_string(piece.parsed, place[2], text)  # type: ignore[union-attr, arg-type]


def visible_text(pieces: list[Piece]) -> str:
    """The page's visible text, one field per line: what attribution and the audit-free figure log read."""
    return "\n".join(_fields(list(pieces)).texts)


class ReportStream:
    def __init__(
        self,
        ctx: TurnContext,
        state: AgentState,
        config: ReportConfig,
        *,
        report_id: str,
        version: int,
        default_title: str,
        labels: FigureLabels,
        buffered: bool,
    ):
        self._ctx = ctx
        self._config = config
        self._context = guard_context(state, ctx)
        self._output_context = guard_context(state, ctx, actions=SENTENCE_OUTPUT_ACTIONS)
        self._locale = state.get("locale") or "ar"
        self._sanitizer = Sanitizer()
        self._renderer = Renderer(self._locale, labels)
        self._labels = labels
        self._buffered = buffered
        self._id = report_id
        self._version = version
        self._default_title = default_title
        self._pending: list[Piece] = []
        self.pieces: list[Piece] = []  # every piece checked so far, in order
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

    async def close(self) -> list[Piece]:
        """Releases what is left, runs the whole-page pass and returns the checked pieces; raises the
        TurnError of a block or an unavailable policy."""
        self._queue.put_nowait(_END)
        try:
            await asyncio.wait([self._task])
        except asyncio.CancelledError:
            await deadline.drain(self._task, name="report:stream")
            raise
        self._task.result()
        await self._whole_page()
        if not self._matched:
            self._ctx.report_guard(
                [GuardEventRecord(checkpoint=Checkpoint.DATA, action="allow", target="answer",
                                  detail="report page, no policy matched")]
            )
        if self._buffered:
            page = Renderer(self._locale, self._labels).render(self.pieces)
            for at in range(0, len(page), BUFFERED_PART_CHARS):
                self._delta(page[at : at + BUFFERED_PART_CHARS])
        return self.pieces

    async def abort(self) -> None:
        await deadline.drain(self._task, name="report:stream")

    @property
    def title(self) -> str:
        return self._renderer.title.strip() or self._title_of(self.pieces) or self._default_title

    # ------------------------------------------------------------------ the queue's side
    async def _run(self) -> None:
        limit = self._config.max_model_chars
        while (item := await self._queue.get()) is not _END:
            if self._fed >= limit:
                self.truncated = True
                continue
            item = item[: limit - self._fed]
            self._fed += len(item)
            self._sanitizer.feed_model(item)
            await self._take(self._sanitizer.drain())
        await self._take(self._sanitizer.finish(), final=True)

    async def _take(self, pieces: list[Piece], *, final: bool = False) -> None:
        self._pending.extend(pieces)
        if self._buffered:
            if final:
                self.pieces.extend(self._pending)
                self._pending = []
            return
        text = sum(len(p.text) for p in self._pending if isinstance(p, Text))
        markup = sum(len(p.html) for p in self._pending if isinstance(p, Markup))
        if final or text >= self._config.stream.flush_chars or markup >= MAX_PENDING_MARKUP or any(
            isinstance(p, Data) for p in self._pending
        ):
            segment, self._pending = self._pending, []
            await self._release(segment)

    async def _release(self, segment: list[Piece]) -> None:
        fields = _fields(segment)
        if fields.texts:
            joined = join_fields("", fields.texts)
            output = await self._check(
                Checkpoint.OUTPUT, {"answer": joined, "citations": []}, self._output_context, "OUTPUT"
            )
            data = await self._check(
                Checkpoint.DATA, DataPayload(text=output.payload["answer"], target="answer"), self._context, "DATA"
            )
            _, checked = split_fields(data.payload.text, "", fields.texts)
            _apply(segment, fields, checked)
        self.pieces.extend(segment)
        html = self._renderer.render(segment)
        if html:
            self._delta(html)

    def _delta(self, html: str) -> None:
        if not self._started:
            self._started = True
            self._ctx.emit("artifact_start", {"id": self._id, "title": self.title, "version": self._version})
        self.streamed.append(html)
        self._ctx.emit("artifact_delta", {"id": self._id, "text": html})

    async def _whole_page(self) -> None:
        """OUTPUT with every action and DATA on the whole page's visible text (ADR-0111's pass)."""
        fields = _fields(self.pieces)
        if not fields.texts:
            return
        joined = join_fields("", fields.texts)
        output = await self._check(Checkpoint.OUTPUT, {"answer": joined, "citations": []}, self._context, "OUTPUT")
        if output.action == "rewrite":
            # A page is not rewritten (it would mean a second 16k-token call); the violation ends the turn.
            raise blocked("output_blocked", output)
        data = await self._check(
            Checkpoint.DATA, DataPayload(text=output.payload["answer"], target="answer"), self._context, "DATA"
        )
        _, checked = split_fields(data.payload.text, "", fields.texts)
        if checked != fields.texts:
            trace.get_current_span().set_attribute("report.final_pass", "redacted")
        _apply(self.pieces, fields, checked)

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

    @staticmethod
    def _title_of(pieces: list[Piece]) -> str:
        return "".join(p.text for p in pieces if isinstance(p, Text) and p.where == "title").strip()
