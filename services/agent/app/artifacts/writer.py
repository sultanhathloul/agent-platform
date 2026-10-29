"""The report writer: the answer node's form on a report turn (reports PR, D1, I1, ADR-0380).

The turn's capabilities ran as on any turn; here the role `report_writer` (heavy tier, 16k output tokens)
writes one HTML page from the same findings and passages, with the design kit's prompt part and, when the
router judged the request an edit of the conversation's latest report, that report's latest version (D4,
ADR-0400); any other report request starts a new report at version 1. The page streams through the guards
(app/artifacts/stream.py) as it is written. A turn draws from one kind of source (ADR-0398): this turn's
findings when knowledge bases are toggled, files attached or a capability found something, else general
knowledge only; nothing of an earlier turn's findings, labels or sources is carried over. Then:

1. citations: page attribution (app/rag/citations.py) over the page's visible text, as for a text answer;
2. source names: the page names no knowledge base, file or publisher outside this turn's citations; a page
   that does is written again once with the names given, then the turn fails (ADR-0398);
3. figures (D2, I3, ADR-0388, ADR-0399): on a sourced turn a figure is labelled with the source whose
   passage holds it with its row or column label (or whose metadata, or the user's message, holds it), and
   any other reads «غير متوفر»; on an unsourced turn every figure is labelled general knowledge;
4. the sources section from this turn's citations only;
5. the 512 KB cap (I4), then the page is stored as the report's next version (I5) and `artifact_done` sent.

The chat's reply is one line naming the report and its version; the page itself never enters the
conversation's messages."""

import logging
import math
import uuid

from langgraph.runtime import Runtime
from opentelemetry import trace

from app.artifacts.config import ReportText
from app.artifacts.document import ModelCall, Renderer, editable, model_strip, sources_section
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
from app.models.gateway import ModelResult
from app.memory.types import MemoryScope
from app.platform_settings import effective
from app.rag.citations import attribute
from app.resilience import deadline
from app.text.arabic import normalize
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


def _capability_name(ctx: TurnContext, slug: str, locale: str) -> str:
    name = ctx.services.registry.get(slug).manifest.name
    return name.en if locale == "en" else name.ar


def sourced_turn(state: AgentState, sources: list[dict]) -> bool:
    """The turn draws from its findings (ADR-0398): knowledge bases toggled, files sent, or a capability that
    returned sources or findings. Otherwise it is a general-knowledge turn."""
    outputs = state.get("capability_outputs") or []
    return bool(
        state.get("selected_kbs") or state.get("turn_attachment_ids") or sources
        or any((o.get("findings") or "").strip() for o in outputs)
    )


def figure_labels(state: AgentState, ctx: TurnContext, sources: list[dict], text: ReportText, *, sourced: bool) -> FigureLabels:
    """Each figure's source from this turn only, first match first (ADR-0388, ADR-0398, ADR-0399): each
    source's passage (with the figure's row or column label) and metadata in order, the capabilities'
    findings, the user's message. An unsourced turn matches nothing: every figure is general knowledge."""
    locale = state["locale"]
    lang = "en" if locale == "en" else "ar"
    labels = FigureLabels(
        Label("general", text.general_label.of(locale)),
        sourced=sourced,
        unavailable=Label("unavailable", text.unavailable_label.of(locale)),
    )
    if not sourced:
        return labels
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
        labels.add([ctx.evidence.get(source.get("ref") or "", "")], label, passage=True)
        labels.add(source_texts(source), label)
    for output in state.get("capability_outputs") or []:
        if output.get("findings"):
            name = _capability_name(ctx, output["capability"], locale)
            labels.add(
                [output["findings"]], Label("capability", text.source_label.of(locale).format(name=name)), passage=True
            )
    labels.add([state.get("user_message") or ""], Label("message", text.message_label.of(locale)))
    return labels


# A name shorter than this, folded, is too likely a common word to be read as a source's name.
MIN_SOURCE_NAME = 6


def _names(item: dict) -> list[str]:
    kb = item.get("kb_name") if isinstance(item.get("kb_name"), dict) else {}
    return [n for n in (kb.get("ar"), kb.get("en"), item.get("title"), item.get("publisher")) if isinstance(n, str) and n]


def known_sources(state: AgentState, previous: list[dict]) -> list[str]:
    """The source names a page could claim without this turn having used them (ADR-0398): the conversation's
    files and what its latest report cited (KB names, titles, publishers). A knowledge base no report cited
    is not listed: its name may be an ordinary phrase («الاقتصاد الدولي») on a page about the subject."""
    names = [a.get("name") or "" for a in state.get("attachments") or []]
    names += [n for c in previous for n in _names(c)]
    return names


def named_sources(text: str, known: list[str], cited: list[dict]) -> list[str]:
    """The known source names the page's visible text names though this turn did not cite them."""
    allowed = {normalize(n) for c in cited for n in _names(c)}
    page = normalize(text)
    found: list[str] = []
    for name in known:
        folded = normalize(name)
        if len(folded) < MIN_SOURCE_NAME or folded in allowed or any(folded in a for a in allowed):
            continue
        if folded in page and name not in found:
            found.append(name)
    return found


def script_failure(text: str, scripts: ScriptPolicy | None) -> dict | None:
    """The page's visible text (its text, the attributes a person reads and report-data's strings) against
    the locale's writing scripts (config/validation.yaml, ADR-0060): None when it keeps to them, else the
    issues and up to five offending words, which the retry's prompt names."""
    issues = script_issues(text, scripts) if scripts else []
    return {"issues": issues, "tokens": script_tokens(text, scripts)[:5]} if issues and scripts else None


def model_calls(ctx: TurnContext, written: ModelResult, locale: str, text: ReportText) -> list[ModelCall]:
    """The call that wrote the page, then each call that produced findings (ADR-0391)."""
    info = text.model_info

    def call(label: str, result: ModelResult) -> ModelCall:
        return ModelCall(label, result.served_model, result.alias, result.duration_s, result.tokens_in, result.tokens_out)

    calls = [call(info.wrote.of(locale), written)]
    for slug, result in ctx.finding_calls:
        name = _capability_name(ctx, slug, locale)
        calls.append(call(info.findings.of(locale).format(name=name), result))
    return calls


async def write_report(state: AgentState, runtime: Runtime[TurnContext]) -> dict:
    ctx = runtime.context
    services = ctx.services
    art, reports = services.artifacts, services.reports
    config = art.report
    locale = state["locale"]
    span = trace.get_current_span()
    principal = ctx.principal
    latest = await reports.latest_in(principal.tenant_id, principal.user_id, state["conversation_id"])
    # An edit of the latest report only when the router judged the request one (ADR-0400); otherwise a new
    # report, version 1, that inherits nothing.
    edit = bool((state.get("output") or {}).get("edit"))
    current = latest if edit else None
    current_html = None
    if current is not None:
        try:
            current_html = editable((await reports.page(current, config.storage.store_s)).decode("utf-8"))
        except ReportUnavailable as exc:
            raise TurnError("report_unavailable", retryable=True, detail=str(exc)) from None
    span.set_attribute("report.edit", current is not None)
    sources = [s for o in state.get("capability_outputs") or [] for s in o.get("sources", [])]
    sourced = sourced_turn(state, sources)
    span.set_attribute("report.sourced", sourced)
    log.info(
        "report turn=%s edit=%s sourced=%s latest=%s",
        ctx.turn_id, current is not None, sourced, latest.report_id if latest else None,
    )

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
        prompt_vars={"has_current": current is not None, "sourced": sourced},
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

    labels = figure_labels(state, ctx, sources, config.text, sourced=sourced)
    known = known_sources(state, latest.citations if latest else [])
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
            text = visible_text(pieces)
            failure = script_failure(text, scripts)
            span.set_attribute(f"report.scripts.attempt{attempt}", "failed" if failure else "passed")
            citations = attribute(text, sources, ctx.evidence, services.citations, question=state.get("user_message") or "")
            # ADR-0398: the page names no source this turn did not cite.
            claimed = named_sources(text, known, citations)
            span.set_attribute(f"report.sources_named.attempt{attempt}", "failed" if claimed else "passed")
            if failure is None and not claimed:
                break
            if attempt == 2:
                issues = (failure or {}).get("issues", []) + (["uncited_source"] if claimed else [])
                raise TurnError("validation_failed", detail="report: " + ",".join(issues))
            vars_ = dict(request.prompt_vars)
            if failure:
                span.set_attribute("report.retry.issues", ",".join(failure["issues"]))
                vars_["script_retry"] = failure
            if claimed:
                span.set_attribute("report.retry.sources", len(claimed))
                vars_["source_retry"] = claimed[:5]
            request = request.model_copy(update={"prompt_vars": vars_})
            prepared = await prepare(request)
        shown = citations
        renderer = Renderer(locale, labels)
        body = renderer.render(pieces)
        end = sources_section(shown, locale, config.text, general=renderer.count.general > 0)
        if art.show_model_info:
            end += model_strip(model_calls(ctx, result, locale, config.text), locale, config.text.model_info)
        page = body + renderer.end(end)
        size = len(page.encode("utf-8"))
        count = renderer.count
        span.set_attribute("report.bytes", size)
        span.set_attribute("report.figures", count.total)
        span.set_attribute("report.figures.general", count.general)
        span.set_attribute("report.figures.unavailable", count.unavailable)
        span.set_attribute("report.truncated", stream.truncated)
        if services.settings.observability_capture_content and count.general_figures:
            span.set_attribute("report.figures.general_values", ",".join(count.general_figures[:200]))
        log.info(
            "report figures turn=%s version=%d figures=%d general=%d unavailable=%d",
            ctx.turn_id, version, count.total, count.general, count.unavailable,
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
