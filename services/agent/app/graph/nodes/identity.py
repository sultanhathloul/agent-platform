from langgraph.runtime import Runtime

from app.errors import TurnError
from app.graph.deps import TurnContext
from app.graph.nodes import traced
from app.graph.state import AgentState


@traced("load_identity_and_state")
async def load_identity_and_state(state: AgentState, runtime: Runtime[TurnContext]) -> dict:
    """Identity comes from the gateway's principal, never from the model or the
    request body. Per-turn fields reset; across-turn fields carry over from the
    checkpoint. A thread that belongs to another user or tenant is refused before
    anything from it is read (defense in depth behind the gateway, ADR-0041)."""
    principal = runtime.context.principal
    owner = (state.get("user_id"), state.get("tenant_id"))
    if owner[0] is not None and owner != (principal.user_id, principal.tenant_id):
        raise TurnError("conversation_forbidden", detail="the thread belongs to another user or tenant")
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
