from langgraph.runtime import Runtime

from app.graph.deps import TurnContext
from app.graph.nodes import traced
from app.graph.state import AgentState


@traced("load_identity_and_state")
async def load_identity_and_state(state: AgentState, runtime: Runtime[TurnContext]) -> dict:
    """Identity comes from the gateway's principal, never from the model or the
    request body. Per-turn fields reset; across-turn fields carry over from the
    checkpoint."""
    principal = runtime.context.principal
    return {
        "user_id": principal.user_id,
        "tenant_id": principal.tenant_id,
        "permissions": list(principal.permissions),
        "turn": (state.get("turn") or 0) + 1,
        "turn_id": runtime.context.turn_id,
        "intent": {},
        "plan": None,
        "iteration": 0,
        "tool_calls": [],
        "retrieved": [],
        "sql_history": [],
        "capability_outputs": [],
        "draft_answer": None,
        "shown_answer": None,
        "citations": [],
        "guard_events": [],
        "reflection": None,
        "decisions": state.get("decisions") or [],
        "open_threads": state.get("open_threads") or [],
        "artifacts": state.get("artifacts") or [],
        "cap_state": state.get("cap_state") or {},
        "tokens_in": state.get("tokens_in") or 0,
        "tokens_out": state.get("tokens_out") or 0,
        "cost_usd": state.get("cost_usd") or 0.0,
    }
