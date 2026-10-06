"""Checkpoint calls from the graph (README §5.2, §10.1). One helper evaluates a
checkpoint for the running turn with its scopes (the principal's tenant, the KBs
selected this turn, the capabilities that ran), reports every event through the
turn context (state, stream, audit delta, metrics) and ends the turn with
`guard_unavailable` when a fail-closed policy could not be evaluated (D8)."""

from collections.abc import Sequence
from typing import Any

from app.errors import TurnError
from app.graph.deps import TurnContext
from app.graph.state import AgentState
from app.guardrails.checkpoints import Checkpoint
from app.guardrails.schema import GuardContext, GuardOutcome


def guard_context(
    state: AgentState,
    ctx: TurnContext,
    *,
    tools: Sequence[str] | None = None,
    actions: frozenset[str] | None = None,
) -> GuardContext:
    capabilities = tools if tools is not None else (state.get("intent") or {}).get("capabilities") or ()
    return GuardContext(
        kbs=tuple(state.get("selected_kbs") or ()),
        tools=tuple(capabilities),
        locale=state.get("locale") or "ar",
        turn=ctx,
        actions=actions,
    )


async def check(
    ctx: TurnContext,
    state: AgentState,
    checkpoint: Checkpoint,
    payload: Any,
    *,
    tools: Sequence[str] | None = None,
    actions: frozenset[str] | None = None,
) -> GuardOutcome:
    outcome = await ctx.services.guard.evaluate(
        checkpoint,
        payload,
        principal=ctx.principal,
        context=guard_context(state, ctx, tools=tools, actions=actions),
    )
    ctx.report_guard(outcome.events)
    if outcome.unavailable:
        raise TurnError("guard_unavailable", retryable=True, detail=f"{checkpoint}: {outcome.unavailable}")
    return outcome


def blocked(code: str, outcome: GuardOutcome) -> TurnError:
    """The turn ends with the deciding policy's own messages (README §10.1)."""
    return TurnError(
        code,
        detail=outcome.decided_by,
        message_ar=outcome.message_ar,
        message_en=outcome.message_en,
        policy_id=outcome.decided_by,
    )
