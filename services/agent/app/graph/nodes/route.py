"""Capability routing (README §5.3). Candidates are the enabled capabilities
the principal holds the permission for. Deterministic manifest rules decide
first; `fallback` capabilities are chosen only when no other rule matched.
When rules leave the choice open among several candidates, the router model
decides; it lands with the second capability (phase 04). The core has no
capability-specific branch: adding a capability changes the candidates, not
this code."""

from collections.abc import Sequence

from langgraph.runtime import Runtime

from app.capabilities.registry import LoadedCapability
from app.errors import TurnError
from app.graph.deps import TurnContext
from app.graph.nodes import traced
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
            else:  # fallback: decided after every other rule
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


@traced("route")
async def route(state: AgentState, runtime: Runtime[TurnContext]) -> dict:
    candidates = runtime.context.services.registry.available_for(runtime.context.principal)
    if not candidates:
        raise TurnError("no_capability_available")
    # The turn request carries no capability toggles until plugin toggles land (phase 05).
    slugs, reasons = select_capabilities(
        candidates, message=state["user_message"], has_attachments=bool(state.get("attachments"))
    )
    if not slugs:
        raise TurnError("no_capability_available", detail="rules matched nothing and no router model yet")
    # Rules do not assess complexity or risk; the router model does.
    return {"intent": {"capabilities": slugs, "decided_by": reasons, "complexity": None, "risk": None}}
