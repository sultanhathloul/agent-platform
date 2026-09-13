from langgraph.runtime import Runtime

from app.graph.deps import TurnContext
from app.graph.nodes import traced
from app.graph.state import AgentState


@traced("persist_state")
async def persist_state(state: AgentState, runtime: Runtime[TurnContext]) -> dict:
    """Appends the finished exchange to the history as the user saw it: the
    streamed text. When a guard changed the draft afterwards, the post-guard
    draft is kept on the same message as `post_guard_draft` (ADR-0039). The
    checkpointer persists the whole state (in-memory in phase 01, Postgres from
    phase 02). Memory writes, online eval and audit flush run after the turn
    (later phases)."""
    draft = state["draft_answer"]
    shown = state.get("shown_answer")
    shown = draft if shown is None else shown
    assistant = {"role": "assistant", "content": shown}
    if draft != shown:
        assistant["post_guard_draft"] = draft
    return {"messages": [{"role": "user", "content": state["user_message"]}, assistant]}
