"""assemble_context (README §5.2): turn-level context preparation. Per-call assembly
happens right before each model call (README §7); here the rolling summary is kept
up to date (README §7 step 4, ADR-0057). Past the trigger, the messages older than
the window are folded into `summary` by the `summarizer` role, through Context
Assembly, and `summary_cursor` moves past them; both are checkpointed."""

from langgraph.runtime import Runtime
from opentelemetry import trace

from app.context.package import AssemblyRequest
from app.context.summarizer import plan_summary
from app.graph.calls import call_model, prepare_call
from app.graph.deps import TurnContext
from app.graph.nodes import traced
from app.graph.state import AgentState


@traced("assemble_context")
async def assemble_context(state: AgentState, runtime: Runtime[TurnContext]) -> dict:
    ctx = runtime.context
    assembler = ctx.services.assembler
    cursor = state.get("summary_cursor") or 0
    summary = state.get("summary")
    plan = plan_summary(
        state.get("messages") or [],
        cursor=cursor,
        summary=summary,
        window_messages=assembler.config.window_messages,
        trigger_tokens=assembler.config.summary.trigger_tokens,
        counter=assembler.counter,
    )
    span = trace.get_current_span()
    span.set_attribute("context.history_messages", plan.history_messages)
    span.set_attribute("context.history_tokens", plan.history_tokens)
    span.set_attribute("context.summary.folded_messages", len(plan.fold))
    if plan.cursor_after == cursor:
        return {}
    if not plan.fold:
        return {"summary_cursor": plan.cursor_after}  # only retracted messages left the window

    node = assembler.node("summarizer")
    request = AssemblyRequest(
        node="summarizer",
        needs=node.needs,
        system_prompt=node.system_prompt,
        user_prompt=node.request_prompt,
        locale=state["locale"],
        state={"summary": summary, "messages": plan.fold},
        window_messages=len(plan.fold),
    )
    result = await call_model(ctx, await prepare_call(ctx, request, role="summarizer"))
    folded = result.text.strip()
    update = {
        "tokens_in": (state.get("tokens_in") or 0) + result.tokens_in,
        "tokens_out": (state.get("tokens_out") or 0) + result.tokens_out,
    }
    if not folded:
        # Nothing came back: keep the cursor, so the same messages are folded next turn.
        span.add_event("context.summary.empty")
        return update
    return {**update, "summary": folded, "summary_cursor": plan.cursor_after}
