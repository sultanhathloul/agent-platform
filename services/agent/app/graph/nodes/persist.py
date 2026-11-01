from langgraph.runtime import Runtime

from app.graph.deps import TurnContext
from app.graph.nodes import traced
from app.graph.state import AgentState


@traced("persist_state")
async def persist_state(state: AgentState, runtime: Runtime[TurnContext]) -> dict:
    """Appends the finished exchange to the history with the post-guard text, the
    text every later model call may see. When a guard changed the draft after it
    streamed (a DATA redaction), the streamed text the user saw is kept on the
    same message as `shown_answer`, for audit only; Context Assembly reads
    `content` (ADR-0048). `turn_id` lets the gateway retract this reply when the
    turn cannot be audited. The question keeps the ids of the files sent with it and
    the reply keeps its checked citations, for the conversation history (ADR-0193);
    Context Assembly reads neither. The checkpointer persists the whole state
    (in-memory in phase 01, Postgres from phase 02). Memory writes, online eval and
    audit flush run after the turn (later phases)."""
    draft = state["draft_answer"]
    shown = state.get("shown_answer")
    assistant = {"role": "assistant", "content": draft, "turn_id": state["turn_id"]}
    if shown is not None and shown != draft:
        assistant["shown_answer"] = shown
    if state.get("citations"):
        assistant["citations"] = list(state["citations"])
    if state.get("report"):
        # The report this reply wrote, {id, kind, title, version}, for the history's report card (I5).
        assistant["artifact"] = dict(state["report"])
    if state.get("skill"):
        # The skill the reply was written with, {slug, version}, for the history's chip (ADR-0301).
        assistant["skill"] = dict(state["skill"])
    question = {"role": "user", "content": state["user_message"]}
    if state.get("turn_attachment_ids"):
        question["attachment_ids"] = list(state["turn_attachment_ids"])
    return {"messages": [question, assistant]}
