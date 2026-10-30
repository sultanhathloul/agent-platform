"""The report writer: the answer node's form on a report turn (reports PR, D1, I1, ADR-0380; option C,
ADR-0406 to ADR-0408).

The turn's capabilities ran as on any turn; here the role `report_writer` (heavy tier, 16k output tokens)
answers with a report spec, JSON matching config/artifacts/report-spec.schema.json, from the same findings
and passages, each passage given an id ("P1", "P2"…), with the spec guide's prompt part and, when the router
judged the request an edit of the conversation's latest report, that report's current spec (ADR-0400); any
other report request starts a new report at version 1. A turn draws from one kind of source (ADR-0398).

Each section streams as it completes, guarded, verified against the passages it cites and rendered by the
platform (app/artifacts/spec_stream.py, verify.py, render.py). Then:

1. the spec must parse and keep to its locale's scripts, and name no source this turn did not use; a reply
   that fails is written again once with the failure named, then the turn ends validation_failed;
2. the page is rendered deterministically from the checked spec: the header, a general-knowledge report's
   one line, the sections, «المصادر» (one entry per document, the elements it supports under it), the model
   strip; the checked spec travels in the page and in the version row;
3. the 512 KB cap (I4), then the version is stored (I5) with the passages its elements cited, for an edit to
   fetch again (ADR-0402), and `artifact_done` sent.

The chat's reply is one line naming the report and its version; the page never enters the conversation."""

import json
import logging
import math
import uuid

from langgraph.runtime import Runtime
from opentelemetry import trace

from app.artifacts.config import ReportText
from app.artifacts.document import ModelCall, model_strip
from app.artifacts.render import PageRenderer, render_page
from app.artifacts.spec import SpecError
from app.artifacts.spec import Chart, KpiRow, ReportSpec, Table
from app.artifacts.spec_stream import NothingFound, SpecStream, visible_text
from app.artifacts.store import ReportUnavailable
from app.artifacts.verify import MISSING, Passage, Verified, Verifier
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


# A name shorter than this, folded, is too likely a common word to be read as a source's name.
MIN_SOURCE_NAME = 6


def _names(item: dict) -> list[str]:
    kb = item.get("kb_name") if isinstance(item.get("kb_name"), dict) else {}
    return [n for n in (kb.get("ar"), kb.get("en"), item.get("title"), item.get("publisher")) if isinstance(n, str) and n]


def passages_for(retrieved: list[dict], sources: list[dict]) -> list[Passage]:
    """This turn's fetched chunks as the spec's passages, P1… in the order the context lists them (ADR-0407)."""
    by_ref = {s.get("ref"): s for s in sources if s.get("ref")}
    out: list[Passage] = []
    for item in retrieved:
        source = by_ref.get(item.get("id"), {})
        out.append(Passage(
            pid=item["pid"], ref=item.get("id") or "", text=item.get("text") or "",
            document_id=item.get("document_id") or source.get("document_id") or "",
            title=source.get("title") or item.get("source") or "", publisher=source.get("publisher"),
            published_on=source.get("published_on"), kb_name=source.get("kb_name"),
            page=item.get("page"), locator=item.get("locator"),
        ))
    return out


def cited(verified: Verified, sources: list[dict]) -> tuple[list[dict], list[dict]]:
    """The version's citations (one per document a value was found in, its pages) and the passages behind
    them as source references without text, for an edit to fetch again (ADR-0402)."""
    by_ref = {s.get("ref"): s for s in sources if s.get("ref")}
    passages = [by_ref[ref] for ref in verified.refs if ref in by_ref][:40]
    citations: list[dict] = []
    for doc in verified.documents:
        first = next((p for p in passages if p.get("document_id") == doc.document_id), {})
        pages = sorted({int(w[1:]) for u in doc.uses for w in u.where if w.startswith("p") and w[1:].isdigit()})
        citations.append({
            "id": doc.document_id, "document_id": doc.document_id, "title": doc.title,
            "source": first.get("kind") or "kb", "kb": first.get("kb"), "kb_name": doc.kb_name,
            "publisher": doc.publisher, "published_on": doc.published_on, "url": first.get("url"),
            "pages": pages, "locator": None,
        })
    return citations, passages


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


def _quoted(items: list[str], language: str) -> str:
    if language == "en":
        return ", ".join(f'"{item}"' for item in items)
    return "، ".join(f"«{item}»" for item in items)


def gaps(spec: ReportSpec) -> int:
    """Values the page shows as «—»: a KPI with no value, a chart point with none, a table's «—» cell."""
    count = 0
    for section in spec.sections:
        if isinstance(section, KpiRow):
            count += sum(1 for item in section.items if item.value is None)
        elif isinstance(section, Chart):
            count += sum(1 for series in section.series for value in series.data if value is None)
        elif isinstance(section, Table):
            count += sum(1 for row in section.rows for cell in row[1:] if cell == MISSING)
    return count


def shortfall(verified: Verified, spec: ReportSpec, text: ReportText, *, edit: bool) -> str:
    """What the report could not do, in one or two plain sentences in its language, or "" (ADR-0416): the
    elements left out for want of data, the categories a chart lost, the values shown as «—»."""
    language = spec.language
    parts: list[str] = []
    if verified.dropped:
        parts.append(text.missing_elements.of(language).format(items=_quoted(list(dict.fromkeys(verified.dropped)), language)))
    for chart, removed in verified.trimmed:
        parts.append(text.trimmed_categories.of(language).format(chart=chart, items=_quoted(removed, language)))
    if verified.removed:
        parts.append(text.removed_sentences.of(language).format(count=len(verified.removed)))
    if gaps(spec):
        parts.append(text.gaps.of(language))
    if not parts:
        return ""
    if edit:
        parts.insert(0, text.partial.of(language))
    return " ".join(parts)


async def source_names(state: AgentState, ctx: TurnContext, locale: str) -> str:
    """The toggled knowledge bases' names (or the turn's files'), for a reply that found nothing in them."""
    names: list[str] = []
    selected = list(state.get("selected_kbs") or [])
    kbs = ctx.services.kbs
    if selected and kbs is not None:
        by_slug = {kb.slug: kb for kb in await kbs.readable(ctx.principal)}
        names += [(by_slug[s].name_en if locale == "en" else by_slug[s].name_ar) if s in by_slug else s for s in selected]
    files = set(state.get("turn_attachment_ids") or [])
    names += [a.get("name") or "" for a in state.get("attachments") or [] if a.get("id") in files]
    return ("، " if locale != "en" else ", ").join(n for n in names if n)


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
    text_config = config.text
    locale = state["locale"]
    span = trace.get_current_span()
    principal = ctx.principal
    latest = await reports.latest_in(principal.tenant_id, principal.user_id, state["conversation_id"])
    # An edit of the latest report only when the router judged the request one (ADR-0400); otherwise a new
    # report, version 1, that inherits nothing.
    edit = bool((state.get("output") or {}).get("edit"))
    current = latest if edit else None
    # The current version's spec (ADR-0406); a version written before option C has none, and its edit
    # writes a whole new spec under the same report.
    current_spec = json.dumps(current.spec, ensure_ascii=False) if current is not None and current.spec else None
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
    # Each passage gets the id the spec cites it by (ADR-0407).
    retrieved = [{**item, "pid": f"P{i}"} for i, item in enumerate(retrieved, start=1)]
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
            current_spec=current_spec,
        ),
        prompt_vars={"has_current": current_spec is not None, "sourced": sourced},
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

    # A source whose text was not fetched this turn (an edit's cited passage from a knowledge base no longer
    # toggled, or one the backend could not return) is not a source of this page (ADR-0402).
    sources = [s for s in sources if not s.get("ref") or s["ref"] in ctx.evidence]
    passages = passages_for(retrieved, sources)
    known = known_sources(state, latest.citations if latest else [])
    buffered = services.guard.requires_buffering(principal, guard_context(state, ctx))
    span.set_attribute("answer.mode", "buffered" if buffered else "streaming")
    # The report's id is known before the page streams, so the panel follows one id across its versions.
    report_id = current.report_id if current else str(uuid.uuid4())
    version = (current.version + 1) if current else 1
    scripts = services.validation.scripts_for(locale)
    kinds = {k: getattr(text_config.kinds, k).of(locale) for k in ("kpi_row", "chart", "table")}
    tokens_in = tokens_out = 0
    with deadline.within(math.inf, margin_s=effective.turn(services.turn).post_answer_reserve_s):
        # The spec must parse (ADR-0406), keep to the locale's scripts (ADR-0390) and name no source this turn
        # did not use (ADR-0398); a failure is written again once with the failure named, then ends the turn.
        for attempt in (1, 2):
            renderer = PageRenderer(locale, text_config, sourced=sourced)
            verifier = Verifier(passages, sourced=sourced, kinds=kinds, untitled=text_config.untitled_element.of(locale),
                                change=text_config.change.of(locale))
            stream = SpecStream(ctx, state, config, renderer, verifier, report_id=report_id, version=version,
                                buffered=buffered)
            try:
                result = await call_model(ctx, prepared, on_token=stream.feed)
            except BaseException:
                await stream.abort()
                raise
            tokens_in += result.tokens_in
            tokens_out += result.tokens_out
            spec = invalid = None
            try:
                spec = await stream.close()
            except NothingFound:
                # The toggled sources hold nothing on the subject (ADR-0416): said plainly, no page saved.
                span.set_attribute("report.nothing_found", True)
                line = text_config.nothing_found.of(locale).format(sources=await source_names(state, ctx, locale))
                log.info("report nothing found turn=%s", ctx.turn_id)
                emit(runtime, "token", {"text": line})
                ctx.answer_sent = True
                return {
                    "answer_mode": "streaming",
                    "draft_answer": line,
                    "shown_answer": line,
                    "citations": [],
                    "guard_events": ctx.guard_records(),
                    "tokens_in": (state.get("tokens_in") or 0) + tokens_in,
                    "tokens_out": (state.get("tokens_out") or 0) + tokens_out,
                }
            except SpecError as exc:
                invalid = str(exc)[:300]
            span.set_attribute(f"report.spec.attempt{attempt}", "invalid" if invalid else "valid")
            text = visible_text(spec)
            failure = script_failure(text, scripts) if spec else None
            span.set_attribute(f"report.scripts.attempt{attempt}", "failed" if failure else "passed")
            verified = verifier.result
            citations, cited_refs = cited(verified, sources) if spec else ([], [])
            claimed = named_sources(text, known, citations) if spec else []
            span.set_attribute(f"report.sources_named.attempt{attempt}", "failed" if claimed else "passed")
            if spec is not None and failure is None and not claimed:
                break
            if attempt == 2:
                issues = (["invalid_spec"] if invalid else []) + (failure or {}).get("issues", [])
                issues += ["uncited_source"] if claimed else []
                raise TurnError("validation_failed", detail="report: " + ",".join(issues))
            vars_ = dict(request.prompt_vars)
            if invalid:
                span.set_attribute("report.retry.spec", invalid)
                vars_["spec_retry"] = invalid
            if failure:
                span.set_attribute("report.retry.issues", ",".join(failure["issues"]))
                vars_["script_retry"] = failure
            if claimed:
                span.set_attribute("report.retry.sources", len(claimed))
                vars_["source_retry"] = claimed[:5]
            request = request.model_copy(update={"prompt_vars": vars_})
            prepared = await prepare(request)
        verified.spec = spec
        renderer = PageRenderer(locale, text_config, sourced=sourced)
        strip = model_strip(model_calls(ctx, result, locale, text_config), locale, text_config.model_info) if art.show_model_info else ""
        page = render_page(spec, renderer, renderer.sources(verified.documents), strip)
        if buffered:
            stream.stream_page(page)
        size = len(page.encode("utf-8"))
        span.set_attribute("report.bytes", size)
        span.set_attribute("report.values", verified.values)
        span.set_attribute("report.values.unavailable", verified.unavailable)
        span.set_attribute("report.dropped", len(verified.dropped))
        span.set_attribute("report.truncated", stream.truncated)
        log.info(
            "report values turn=%s version=%d sections=%d values=%d found=%d unavailable=%d dropped=%s",
            ctx.turn_id, version, len(spec.sections), verified.values, verified.found, verified.unavailable,
            verified.dropped,
        )
        if size > config.max_bytes:
            raise TurnError("report_too_large", detail=f"{size} bytes over {config.max_bytes}")
        title = spec.title[:300] or text_config.untitled.of(locale)
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
                citations=citations,
                labels={},
                figures=verified.values,
                passages=cited_refs,
                general=0 if sourced else verified.values,
                spec=spec.model_dump(mode="json", exclude_none=True),
                timeout_s=config.storage.store_s,
            )
        except ReportUnavailable as exc:
            raise TurnError("report_unavailable", retryable=True, detail=str(exc)) from None
    emit(runtime, "artifact_done", {"id": record.report_id, "version": record.version, "size": record.size,
                                    "title": record.title})
    line = (text_config.updated if current else text_config.created).of(locale).format(
        title=record.title, version=record.version
    )
    # Never silent (ADR-0416): what could not be done, after the line naming the report.
    missing = shortfall(verified, spec, text_config, edit=current is not None)
    if missing:
        line = f"{line} {missing}"
        span.set_attribute("report.shortfall", True)
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
