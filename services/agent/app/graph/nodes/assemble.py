"""The rolling summary (README §7 step 4, ADR-0057, ADR-0073). Past the trigger, the
messages older than the window are folded into `summary` by the `summarizer` role,
through Context Assembly, and `summary_cursor` moves past them; both are
checkpointed.

The fold runs at the end of the turn, in `fold_summary` after persist_state and
before write_memory, so it never delays the first token. assemble_context, at the
start of the turn, keeps a lazy fold as the fallback: it folds only when the
history already exceeds the trigger there, e.g. after an end-of-turn fold failed.
Per-call assembly happens right before each model call (README §7)."""

import logging

from langgraph.runtime import Runtime
from opentelemetry import trace

from app.context.package import AssemblyRequest
from app.context.summarizer import SummaryPlan, plan_summary
from app.graph.calls import call_model, prepare_call
from app.graph.deps import TurnContext
from app.graph.nodes import traced
from app.graph.state import AgentState

log = logging.getLogger(__name__)


def _plan(state: AgentState, ctx: TurnContext) -> SummaryPlan:
    assembler = ctx.services.assembler
    plan = plan_summary(
        state.get("messages") or [],
        cursor=state.get("summary_cursor") or 0,
        summary=state.get("summary"),
        window_messages=assembler.config.window_messages,
        trigger_tokens=assembler.config.summary.trigger_tokens,
        counter=assembler.counter,
    )
    span = trace.get_current_span()
    span.set_attribute("context.history_messages", plan.history_messages)
    span.set_attribute("context.history_tokens", plan.history_tokens)
    span.set_attribute("context.summary.folded_messages", len(plan.fold))
    return plan


async def _fold(state: AgentState, ctx: TurnContext, plan: SummaryPlan) -> dict:
    if plan.cursor_after == (state.get("summary_cursor") or 0):
        return {}
    if not plan.fold:
        return {"summary_cursor": plan.cursor_after}  # only retracted messages left the window

    node = ctx.services.assembler.node("summarizer")
    summary = state.get("summary")
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
        # Nothing came back: keep the cursor, so the same messages are folded next time.
        trace.get_current_span().add_event("context.summary.empty")
        return update
    return {**update, "summary": folded, "summary_cursor": plan.cursor_after}


@traced("assemble_context")
async def assemble_context(state: AgentState, runtime: Runtime[TurnContext]) -> dict:
    """Lazy fallback: folds before the answer only when the history already exceeds
    the trigger at the start of the turn."""
    ctx = runtime.context
    plan = _plan(state, ctx)
    if plan.history_tokens <= ctx.services.assembler.config.summary.trigger_tokens:
        return {}
    trace.get_current_span().set_attribute("context.summary.lazy", bool(plan.fold))
    return await _fold(state, ctx, plan)


@traced("fold_summary")
async def fold_summary(state: AgentState, runtime: Runtime[TurnContext]) -> dict:
    """Folds after the reply is persisted, ready for the next turn. The reply is already
    stored, so a failure is recorded on the span and never fails the turn; the
    messages stay unfolded and the next fold takes them."""
    ctx = runtime.context
    span = trace.get_current_span()
    try:
        return await _fold(state, ctx, _plan(state, ctx))
    except Exception as exc:
        span.add_event("context.summary.fold_failed", {"error": type(exc).__name__})
        log.warning("summary fold failed for turn %s: %s", ctx.turn_id, type(exc).__name__)
        return {}
