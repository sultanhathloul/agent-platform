"""The report writer: the answer node's form on a report turn (reports PR, D1, I1, ADR-0380).

The turn's capabilities ran as on any turn; here the role `report_writer` (heavy tier, 16k output tokens)
writes one HTML page from the same findings and passages, with the design kit's prompt part and, on a
follow-up, the latest version of the conversation's report (D4). The page streams through the guards
(app/artifacts/stream.py) as it is written. Then:

1. citations: page attribution (app/rag/citations.py) over the page's visible text, as for a text answer;
2. figures (D2, I3, ADR-0388): every figure carries one source label: the knowledge base or file whose
   passage or metadata holds it, the capability whose findings hold it, the user's message, on a follow-up
   the label the previous version gave it, or else general knowledge;
3. the sources section from the citations, with a follow-up keeping the previous version's;
4. the 512 KB cap (I4), then the page is stored as the report's next version (I5) and `artifact_done` sent.

The chat's reply is one line naming the report and its version; the page itself never enters the
conversation's messages."""

import logging
import math
import uuid

from langgraph.runtime import Runtime
from opentelemetry import trace

from app.artifacts.config import ReportText
from app.artifacts.document import Renderer, editable, sources_section
from app.artifacts.figures import FigureLabels, Label, source_texts
from app.artifacts.store import ReportUnavailable
from app.artifacts.stream import ReportStream, visible_text
from app.context.package import BLOCK_NAMES, AssemblyRequest, ReportBlockRequest
from app.context.selectors.blocks import ReportTooLargeToEdit
from app.errors import TurnError
from app.graph.calls import call_model, prepare_call, tier_event
from app.graph.deps import TurnContext
from app.graph.evidence import load_evidence
from app.graph.guarding import guard_context
from app.graph.nodes import emit
from app.graph.state import AgentState
from app.memory.types import MemoryScope
from app.platform_settings import effective
from app.rag.citations import attribute
from app.resilience import deadline
from app.text.script import ScriptPolicy, script_issues, script_tokens

log = logging.getLogger(__name__)

ROLE = "report_writer"
NODE = "report"


def report_needs(state: AgentState, ctx: TurnContext) -> list[str]:
    """The report writer's blocks: the turn's capabilities' needs (as the answer node's, without the
    skill block, D1), the kit and the current version."""
    default = ctx.services.assembler.node(NODE).needs
    wanted: set[str] = {"report_kit", "report_current"}
    for slug in state["intent"]["capabilities"]:
        wanted.update(ctx.services.registry.get(slug).manifest.context.needs_or(default))
    wanted.discard("skill")
    return [block for block in BLOCK_NAMES if block in wanted]


def figure_labels(
    state: AgentState, ctx: TurnContext, sources: list[dict], previous: dict, text: ReportText
) -> FigureLabels:
    """Each figure's source, first match first (ADR-0388): the passages and metadata of the sources in
    order, the capabilities' findings, the user's message, the previous version's labels."""
    locale = state["locale"]
    lang = "en" if locale == "en" else "ar"
    labels = FigureLabels(Label("general", text.general_label.of(locale)))
    for source in sources:
        names = source.get("kb_name") if isinstance(source.get("kb_name"), dict) else None
        if names and names.get(lang):
            kind, name = "kb", names[lang]
        elif source.get("kind", "doc") == "doc" and source.get("title"):
            kind, name = "file", source["title"]
        else:
            kind, name = "source", source.get("title") or ""
        if not name:
            continue
        label = Label(kind, text.source_label.of(locale).format(name=name))
        labels.add([ctx.evidence.get(source.get("ref") or "", ""), *source_texts(source)], label)
    for output in state.get("capability_outputs") or []:
        if output.get("findings"):
            name = ctx.services.registry.get(output["capability"]).manifest.name.of(locale)
            labels.add([output["findings"]], Label("capability", text.source_label.of(locale).format(name=name)))
    labels.add([state.get("user_message") or ""], Label("message", text.message_label.of(locale)))
    labels.keep(previous)
    return labels


def script_failure(text: str, scripts: ScriptPolicy | None) -> dict | None:
    """The page's visible text (its text, the attributes a person reads and report-data's strings) against
    the locale's writing scripts (config/validation.yaml, ADR-0060): None when it keeps to them, else the
    issues and up to five offending words, which the retry's prompt names."""
    issues = script_issues(text, scripts) if scripts else []
    return {"issues": issues, "tokens": script_tokens(text, scripts)[:5]} if issues and scripts else None


def _merged(current: list[dict], new: list[dict]) -> list[dict]:
    """The sources section of a follow-up: the previous version's sources, then this turn's new ones."""
    seen = {c.get("id") for c in new}
    return [*new, *(c for c in current if c.get("id") not in seen)]


async def write_report(state: AgentState, runtime: Runtime[TurnContext]) -> dict:
    ctx = runtime.context
    services = ctx.services
    art, reports = services.artifacts, services.reports
    config = art.report
    locale = state["locale"]
    span = trace.get_current_span()
    principal = ctx.principal
    current = await reports.latest_in(principal.tenant_id, principal.user_id, state["conversation_id"])
    current_html = None
    if current is not None:
        try:
            current_html = editable((await reports.page(current, config.storage.store_s)).decode("utf-8"))
        except ReportUnavailable as exc:
            raise TurnError("report_unavailable", retryable=True, detail=str(exc)) from None
    span.set_attribute("report.edit", current is not None)

    needs = report_needs(state, ctx)
    retrieved = await load_evidence(state, ctx) if "retrieval" in needs else []
    selected = [services.registry.get(slug) for slug in state["intent"]["capabilities"]]
    node = services.assembler.node(NODE)
    request = AssemblyRequest(
        node=NODE,
        needs=needs,
        system_prompt=node.system_prompt,
        locale=locale,
        state=dict(state),
        findings=[{"capability": o["capability"], "findings": o["findings"]} for o in state["capability_outputs"]],
        retrieved=list(retrieved),
        report=ReportBlockRequest(
            kit_id=art.design.id,
            kit_version=art.design.version,
            kit_text=art.design.text,
            current_title=current.title if current else None,
            current_version=current.version if current else None,
            current_html=current_html,
        ),
        prompt_vars={"has_current": current is not None},
        memory_scope=MemoryScope.of(principal),
    )

    async def prepare(request: AssemblyRequest):
        try:
            prepared = await prepare_call(ctx, request, role=ROLE, step=NODE, capabilities=selected)
        except ReportTooLargeToEdit as exc:
            raise TurnError("report_edit_too_large", detail=str(exc)) from None
        emit(runtime, "tier", tier_event(prepared.decision))
        return prepared

    prepared = await prepare(request)

    sources = [s for o in state.get("capability_outputs") or [] for s in o.get("sources", [])]
    labels = figure_labels(state, ctx, sources, current.figure_labels if current else {}, config.text)
    buffered = services.guard.requires_buffering(principal, guard_context(state, ctx))
    span.set_attribute("answer.mode", "buffered" if buffered else "streaming")
    # The report's id is known before the page streams, so the panel follows one id across its versions.
    report_id = current.report_id if current else str(uuid.uuid4())
    version = (current.version + 1) if current else 1
    title_default = current.title if current else config.text.untitled.of(locale)
    scripts = services.validation.scripts_for(locale)
    tokens_in = tokens_out = 0
    with deadline.within(math.inf, margin_s=effective.turn(services.turn).post_answer_reserve_s):
        # D of the owner's decisions on the branch (ADR-0390): the page keeps to its locale's scripts, as a
        # text answer does; a failure is written again once with the failure named, then ends the turn.
        for attempt in (1, 2):
            stream = ReportStream(
                ctx, state, config, report_id=report_id, version=version, default_title=title_default,
                labels=labels, buffered=buffered,
            )
            try:
                result = await call_model(ctx, prepared, on_token=stream.feed)
            except BaseException:
                await stream.abort()
                raise
            pieces = await stream.close()
            tokens_in += result.tokens_in
            tokens_out += result.tokens_out
            failure = script_failure(visible_text(pieces), scripts)
            span.set_attribute(f"report.scripts.attempt{attempt}", "failed" if failure else "passed")
            if failure is None:
                break
            if attempt == 2:
                raise TurnError("validation_failed", detail="report: " + ",".join(failure["issues"]))
            span.set_attribute("report.retry.issues", ",".join(failure["issues"]))
            vars_ = {**request.prompt_vars, "script_retry": failure}
            request = request.model_copy(update={"prompt_vars": vars_})
            prepared = await prepare(request)
        text = visible_text(pieces)
        citations = attribute(text, sources, ctx.evidence, services.citations, question=state.get("user_message") or "")
        shown = _merged(current.citations, citations) if current else citations
        renderer = Renderer(locale, labels)
        body = renderer.render(pieces)
        page = body + renderer.end(sources_section(shown, locale, config.text, general=renderer.count.general > 0))
        size = len(page.encode("utf-8"))
        count = renderer.count
        span.set_attribute("report.bytes", size)
        span.set_attribute("report.figures", count.total)
        span.set_attribute("report.figures.general", count.general)
        span.set_attribute("report.truncated", stream.truncated)
        if services.settings.observability_capture_content and count.general_figures:
            span.set_attribute("report.figures.general_values", ",".join(count.general_figures[:200]))
        log.info(
            "report figures turn=%s version=%d figures=%d general=%d",
            ctx.turn_id, version, count.total, count.general,
        )
        if size > config.max_bytes:
            raise TurnError("report_too_large", detail=f"{size} bytes over {config.max_bytes}")
        title = (stream.title or title_default)[:300]
        try:
            record = await reports.save_version(
                report_id=report_id,
                new=current is None,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                conversation_id=state["conversation_id"],
                title=title,
                page=page.encode("utf-8"),
                locale="en" if locale == "en" else "ar",
                turn_id=ctx.turn_id,
                citations=shown,
                labels={figure: label.stored() for figure, label in sorted(count.sourced.items())},
                figures=count.total,
                general=count.general,
                timeout_s=config.storage.store_s,
            )
        except ReportUnavailable as exc:
            raise TurnError("report_unavailable", retryable=True, detail=str(exc)) from None
    emit(runtime, "artifact_done", {"id": record.report_id, "version": record.version, "size": record.size,
                                    "title": record.title})
    line = (config.text.updated if current else config.text.created).of(locale).format(
        title=record.title, version=record.version
    )
    emit(runtime, "token", {"text": line})
    ctx.answer_sent = True
    artifact = {"id": record.report_id, "kind": "report", "title": record.title, "version": record.version}
    others = [a for a in state.get("artifacts") or [] if a.get("id") != record.report_id]
    return {
        "answer_mode": "streaming",
        "draft_answer": line,
        "shown_answer": line,
        "citations": citations,
        "artifacts": [*others, artifact],
        "report": artifact,
        "guard_events": ctx.guard_records(),
        "tokens_in": (state.get("tokens_in") or 0) + tokens_in,
        "tokens_out": (state.get("tokens_out") or 0) + tokens_out,
    }
