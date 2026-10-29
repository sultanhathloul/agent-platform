"""Capability routing (README §5.3). Candidates are the enabled capabilities
the principal holds the permission for. Deterministic manifest rules decide
first; `fallback` capabilities are chosen only when no other rule matched.
When no rule decided, the capabilities declaring `conversation_attachments` (when the
conversation holds indexed files) or `keyword_candidate` (when one of their keywords is in the
message) become candidates, and the router model chooses among them and the fallback
capabilities (phase 04, ADR-0139, ADR-0145; phase 05, ADR-0216). A keyword alone never selects
such a capability, and a turn with no candidate costs no router call.
Phase 08 (I3): the rule `kbs_selected` selects a capability when the turn carries knowledge bases
the user toggled, admitted as permitted and live; with none, it never matches.
Phase 06 (ADR-0299): the same node selects at most one skill: the picker's, or one the router
model chooses among the skills whose keywords match. A turn with neither adds no router call.
Reports PR (ADR-0378): the same node decides the answer's form. The composer's output picker decides
alone; otherwise a turn admitted on the report profile (a report keyword in the message, or a conversation
that holds a report, D3, D4) is a report candidate, and the router model returns `output`. A report turn
applies no skill (D1).
The core has no capability-specific branch: adding a capability changes the
candidates, not this code."""

import logging
from collections.abc import Sequence

from langgraph.runtime import Runtime
from opentelemetry import trace

from app.capabilities.registry import LoadedCapability
from app.errors import TurnError
from app.graph.deps import TurnContext
from app.graph.nodes import traced
from app.graph.router import OutputCandidate, choose
from app.graph.state import AgentState
from app.skills.registry import LoadedSkill
from app.text.arabic import contains_keyword

MAX_CAPABILITIES = 3

log = logging.getLogger(__name__)



async def _current_sections(state: AgentState, ctx: TurnContext) -> tuple[str, ...]:
    """The conversation's latest report's section titles, from its stored spec: its subject, which the router
    compares the request with to tell an edit from a new report (owner decision 2 on PR #17, ADR-0412)."""
    reports = ctx.services.reports
    if reports is None:
        return ()
    principal = ctx.principal
    try:
        latest = await reports.latest_in(principal.tenant_id, principal.user_id, state["conversation_id"])
    except Exception:  # the store unreachable: the title alone, as before
        return ()
    spec = (latest.spec or {}) if latest else {}
    titles = [s.get("title") for s in spec.get("sections") or [] if isinstance(s, dict) and s.get("title")]
    return tuple(str(t)[:120] for t in titles[:12])

def select_capabilities(
    candidates: Sequence[LoadedCapability],
    *,
    message: str,
    has_attachments: bool,
    toggled: Sequence[str] = (),
    kbs_selected: bool = False,
) -> tuple[list[str], list[str]]:
    """Returns (slugs in manifest ui.order, reasons)."""
    selected: list[str] = []
    reasons: list[str] = []
    for cap in candidates:
        hints = cap.manifest.routing_hints
        for rule in hints.rules:
            if rule.when == "toggled":
                matched = cap.slug in toggled
            elif rule.when == "keyword":
                matched = any(contains_keyword(message, k) for k in [*hints.keywords.ar, *hints.keywords.en])
            elif rule.when == "attachments":
                matched = has_attachments
            elif rule.when == "kbs_selected":
                matched = kbs_selected
            else:  # fallback, conversation_attachments, keyword_candidate: after every other rule
                continue
            if matched:
                selected.append(cap.slug)
                reasons.append(f"{cap.slug}:{rule.when}")
                break
    if not selected:
        for cap in candidates:
            if any(rule.when == "fallback" for rule in cap.manifest.routing_hints.rules):
                selected.append(cap.slug)
                reasons.append(f"{cap.slug}:fallback")
    return selected[:MAX_CAPABILITIES], reasons[:MAX_CAPABILITIES]


def _keyword_in(cap: LoadedCapability, message: str) -> bool:
    keywords = cap.manifest.routing_hints.keywords
    return any(contains_keyword(message, k) for k in [*keywords.ar, *keywords.en])


def router_candidates(
    candidates: Sequence[LoadedCapability], *, has_indexed: bool, message: str = ""
) -> list[LoadedCapability]:
    """Capabilities the router model may choose: those whose `conversation_attachments` rule holds
    (the conversation holds indexed files) or whose `keyword_candidate` rule holds (one of their
    keywords is in the message)."""
    pool = []
    for cap in candidates:
        for rule in cap.manifest.routing_hints.rules:
            if (rule.when == "conversation_attachments" and has_indexed) or (
                rule.when == "keyword_candidate" and _keyword_in(cap, message)
            ):
                pool.append(cap)
                break
    return pool


def _skill_state(skill: LoadedSkill | None) -> dict | None:
    return None if skill is None else {"slug": skill.slug, "version": skill.manifest.version}


def _drop(span, slug: str, reason: str) -> None:
    """A skill the router named that cannot apply (ADR-0299): dropped, logged and on the span."""
    log.info("skill_dropped skill=%s reason=%s", slug, reason)
    span.add_event("skill_dropped", {"skill": slug, "reason": reason})


@traced("route")
async def route(state: AgentState, runtime: Runtime[TurnContext]) -> dict:
    ctx = runtime.context
    services = ctx.services
    candidates = services.registry.available_for(ctx.principal)
    if not candidates:
        raise TurnError("no_capability_available")
    span = trace.get_current_span()
    message = state["user_message"]
    # Knowledge base toggles (phase 08, I3): the turn's selected KBs, admitted as permitted and live.
    slugs, reasons = select_capabilities(
        candidates,
        message=message,
        has_attachments=bool(state.get("turn_attachment_ids")),
        kbs_selected=bool(state.get("selected_kbs")),
    )
    complexity = risk = None
    # Skills (phase 06, ADR-0299): (a) the picker's skill, applied with no router decision on it;
    # (b) otherwise the available skills whose keywords match are candidates the router model may
    # choose one of; (c) no picker and no candidate: no skill and no router call added.
    available = services.skills.available_for(ctx.principal, [c.slug for c in candidates])
    requested = state.get("requested_skill")
    skill: LoadedSkill | None = None
    skill_source = None
    skill_candidates: list[LoadedSkill] = []
    if requested:
        skill = next((s for s in available if s.slug == requested), None)
        skill_source = "picker"
        if skill is None:  # admission refused it already (skill_forbidden); defense in depth
            _drop(span, requested, "not_available")
    else:
        skill_candidates = services.skills.candidates(available, message)
    fallback_only = all(reason.endswith(":fallback") for reason in reasons)
    has_indexed = any(a["status"] == "indexed" for a in state.get("attachments") or [])
    pool = router_candidates(candidates, has_indexed=has_indexed, message=message) if fallback_only else []
    # The answer's form (ADR-0378): the picker's, else the router model's on a report candidate.
    picked = state.get("requested_output")
    output = {"form": picked, "decided_by": "picker"} if picked in ("text", "report") else {"form": "text", "decided_by": "none"}
    candidate = None
    reports = [a for a in state.get("artifacts") or [] if a.get("kind") == "report"]
    sections = await _current_sections(state, ctx) if reports and (picked in (None, "report")) else ()
    if picked is None and ctx.profile == "report":
        candidate = OutputCandidate(current=reports[-1]["title"] if reports else None, sections=sections)
    elif picked == "report" and reports:
        # The picker chose a report and the conversation holds one: the router's call says whether this
        # request edits it or starts a new one (ADR-0400).
        candidate = OutputCandidate(current=reports[-1]["title"], picked=True, sections=sections)
    if pool or skill_candidates or candidate is not None:
        if pool:
            fallback = [
                c for c in candidates if any(r.when == "fallback" for r in c.manifest.routing_hints.rules)
            ]
            choice, others = pool, fallback
        else:
            # The rules decided the capabilities; the router model is asked for the skill only, among
            # the capabilities already chosen (its capability answer is not used).
            choice, others = [services.registry.get(slug) for slug in slugs], []
        decision = await choose(
            ctx, dict(state), choice, others, services.router, skills=skill_candidates, output=candidate
        )
        if candidate is not None and not candidate.picked:
            output = {"form": decision.output or "text", "decided_by": decision.source}
        if candidate is not None and candidate.current:
            output["edit"] = bool(decision.edit)
            output["edit_decided_by"] = decision.source
        if pool:
            slugs = decision.capabilities[:MAX_CAPABILITIES]
            reasons = [f"{slug}:{decision.source}" for slug in slugs]
            complexity, risk = decision.complexity, decision.risk
        if decision.dropped_skill:
            _drop(span, decision.dropped_skill, "not_a_candidate")
        if decision.skill:
            skill = services.skills.get(decision.skill)
            skill_source = decision.source
    if not slugs:
        raise TurnError("no_capability_available", detail="rules matched nothing and no router candidate")
    if skill is not None and output["form"] == "report":
        _drop(span, skill.slug, "report_turn")  # D1: a report turn applies no text skill
        skill = None
    if skill is not None and skill_source != "picker":
        missing = set(skill.manifest.requires.capabilities) - set(slugs)
        if missing:
            _drop(span, skill.slug, "required_capability_not_selected")
            skill = None
    span.set_attribute("route.capabilities", ",".join(slugs))
    span.set_attribute("route.decided_by", ",".join(reasons))
    span.set_attribute("route.skill_candidates", ",".join(s.slug for s in skill_candidates))
    if skill is not None:
        span.set_attribute("route.skill", skill.manifest.ref)  # id@version, never its text (G8)
        span.set_attribute("route.skill.decided_by", skill_source or "")
    span.set_attribute("route.output", output["form"])
    span.set_attribute("route.output.decided_by", output["decided_by"])
    if "edit" in output:
        span.set_attribute("route.output.edit", output["edit"])
        span.set_attribute("route.output.edit_decided_by", output["edit_decided_by"])
        log.info(
            "route report turn=%s form=%s edit=%s decided_by=%s current=%r",
            ctx.turn_id, output["form"], output["edit"], output["edit_decided_by"], candidate.current if candidate else None,
        )
    return {
        "intent": {"capabilities": slugs, "decided_by": reasons, "complexity": complexity, "risk": risk},
        "skill": _skill_state(skill),
        "output": output,
    }
