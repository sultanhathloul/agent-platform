"""Graph-level guard checkpoints: INPUT (1), TOOL_LIST (2), OUTPUT (5), DATA (6).
PRE_TOOL/POST_TOOL run in the broker, MODEL_TIER in the tier resolver.

With no policies loaded (phase 01) every checkpoint allows. Block, redact and
rewrite handling arrives with the policy engine in phase 03.
"""

from langgraph.runtime import Runtime

from app.graph.deps import TurnContext
from app.graph.nodes import emit, traced
from app.graph.state import AgentState
from app.guardrails.checkpoints import Checkpoint
from app.guardrails.schema import GuardOutcome


def _record(state: AgentState, runtime: Runtime[TurnContext], outcome: GuardOutcome) -> list[dict]:
    events = list(state.get("guard_events") or [])
    for event in outcome.events:
        record = event.model_dump(mode="json")
        events.append(record)
        if event.action != "allow":
            emit(runtime, "guard", record)
    return events


@traced("guard.INPUT")
async def guard_input(state: AgentState, runtime: Runtime[TurnContext]) -> dict:
    outcome = await runtime.context.services.guard.evaluate(
        Checkpoint.INPUT,
        {"message": state["user_message"], "attachments": state.get("attachments") or []},
        principal=runtime.context.principal,
    )
    return {"guard_events": _record(state, runtime, outcome)}


@traced("guard.TOOL_LIST")
async def guard_tool_list(state: AgentState, runtime: Runtime[TurnContext]) -> dict:
    """Keeps only routed capabilities the principal holds the permission for
    (defense in depth after routing), then lets policies filter further."""
    ctx = runtime.context
    registry = ctx.services.registry
    permitted = [
        s for s in state["intent"]["capabilities"] if ctx.principal.has(registry.get(s).manifest.permission)
    ]
    outcome = await ctx.services.guard.evaluate(Checkpoint.TOOL_LIST, permitted, principal=ctx.principal)
    return {
        "intent": {**state["intent"], "capabilities": list(outcome.payload)},
        "guard_events": _record(state, runtime, outcome),
    }


@traced("guard.OUTPUT")
async def guard_output(state: AgentState, runtime: Runtime[TurnContext]) -> dict:
    outcome = await runtime.context.services.guard.evaluate(
        Checkpoint.OUTPUT,
        {"answer": state["draft_answer"], "citations": state.get("citations") or []},
        principal=runtime.context.principal,
    )
    return {"guard_events": _record(state, runtime, outcome)}


@traced("guard.DATA")
async def guard_data(state: AgentState, runtime: Runtime[TurnContext]) -> dict:
    outcome = await runtime.context.services.guard.evaluate(
        Checkpoint.DATA, state["draft_answer"], principal=runtime.context.principal
    )
    return {"draft_answer": outcome.payload, "guard_events": _record(state, runtime, outcome)}
