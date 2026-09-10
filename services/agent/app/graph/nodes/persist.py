from langgraph.runtime import Runtime

from app.graph.deps import TurnContext
from app.graph.nodes import traced
from app.graph.state import AgentState


@traced("persist_state")
async def persist_state(state: AgentState, runtime: Runtime[TurnContext]) -> dict:
    """Appends the finished exchange to the history. The checkpointer persists
    the whole state (in-memory in phase 01, Postgres from phase 02). Memory
    writes, online eval and audit flush run after the turn (later phases)."""
    return {
        "messages": [
            {"role": "user", "content": state["user_message"]},
            {"role": "assistant", "content": state["draft_answer"]},
        ]
    }
