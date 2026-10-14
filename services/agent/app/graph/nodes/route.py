"""Capability routing (README §5.3). Candidates are the enabled capabilities
the principal holds the permission for. Deterministic manifest rules decide
first; `fallback` capabilities are chosen only when no other rule matched.
When no rule decided and the conversation holds indexed files, the capabilities
declaring `conversation_attachments` become candidates and the router model
chooses among them and the fallback capabilities (phase 04, ADR-0139, ADR-0145).
The core has no capability-specific branch: adding a capability changes the
candidates, not this code."""

from collections.abc import Sequence

from langgraph.runtime import Runtime
from opentelemetry import trace

from app.capabilities.registry import LoadedCapability
from app.errors import TurnError
from app.graph.deps import TurnContext
from app.graph.nodes import traced
from app.graph.router import choose
from app.graph.state import AgentState
from app.text.arabic import contains_keyword

MAX_CAPABILITIES = 3


def select_capabilities(
    candidates: Sequence[LoadedCapability],
    *,
    message: str,
    has_attachments: bool,
    toggled: Sequence[str] = (),
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
            else:  # fallback and conversation_attachments: decided after every other rule
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


def router_candidates(candidates: Sequence[LoadedCapability], *, has_indexed: bool) -> list[LoadedCapability]:
    """Capabilities the router model may choose because the conversation holds indexed files."""
    if not has_indexed:
        return []
    return [
        c
        for c in candidates
        if any(r.when == "conversation_attachments" for r in c.manifest.routing_hints.rules)
    ]


@traced("route")
async def route(state: AgentState, runtime: Runtime[TurnContext]) -> dict:
    candidates = runtime.context.services.registry.available_for(runtime.context.principal)
    if not candidates:
        raise TurnError("no_capability_available")
    # The turn request carries no capability toggles until plugin toggles land (phase 05).
    slugs, reasons = select_capabilities(
        candidates, message=state["user_message"], has_attachments=bool(state.get("turn_attachment_ids"))
    )
    complexity = risk = None
    if all(reason.endswith(":fallback") for reason in reasons):
        has_indexed = any(a["status"] == "indexed" for a in state.get("attachments") or [])
        pool = router_candidates(candidates, has_indexed=has_indexed)
        if pool:
            fallback = [
                c for c in candidates if any(r.when == "fallback" for r in c.manifest.routing_hints.rules)
            ]
            decision = await choose(
                runtime.context, dict(state), pool, fallback, runtime.context.services.router
            )
            slugs = decision.capabilities[:MAX_CAPABILITIES]
            reasons = [f"{slug}:{decision.source}" for slug in slugs]
            complexity, risk = decision.complexity, decision.risk
    if not slugs:
        raise TurnError("no_capability_available", detail="rules matched nothing and no router candidate")
    span = trace.get_current_span()
    span.set_attribute("route.capabilities", ",".join(slugs))
    span.set_attribute("route.decided_by", ",".join(reasons))
    return {"intent": {"capabilities": slugs, "decided_by": reasons, "complexity": complexity, "risk": risk}}
