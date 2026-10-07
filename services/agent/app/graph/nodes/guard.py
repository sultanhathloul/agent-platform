"""Graph-level guard checkpoints: INPUT (1), TOOL_LIST (2), OUTPUT (5), DATA (6).
PRE_TOOL/POST_TOOL run in the broker, MODEL_TIER in the tier resolver (README §10.1).

- INPUT: a block ends the turn with the policy's message (`input_blocked`); a
  redaction replaces the user message before any model sees it.
- TOOL_LIST: routed capabilities the principal has no permission for are dropped
  (defense in depth), then each is evaluated under its own `tool:{slug}` scope.
- Buffered mode (D3): OUTPUT and DATA run on the whole answer before any text left; a
  block ends the turn, a `rewrite` asks the model once more with the violation
  explained, and a redaction changes the text release_answer streams.
- Streaming mode: the OUTPUT redact policies and DATA already ran on every sentence
  before it was released (ADR-0106). Both still run once on the whole released text
  (ADR-0111): a span the sentence buffer split (a number broken by a newline) is caught
  there, and the released text is retracted and re-sent redacted (the ADR-0039 path),
  recorded on the node's span. A blocking verdict there (a hot reload mid-turn, or a
  match no sentence held) retracts the text and ends the turn.
"""

from langgraph.runtime import Runtime
from opentelemetry import trace

from app.errors import TurnError
from app.graph.calls import call_model
from app.graph.deps import TurnContext
from app.graph.guarding import blocked, check, guard_context
from app.graph.nodes import emit, retract_streamed, traced
from app.graph.nodes.answer import prepare_answer
from app.graph.nodes.validate import validation_issues
from app.graph.state import AgentState
from app.graph.streaming import split_sentences
from app.guardrails.checkpoints import Checkpoint
from app.guardrails.schema import DataPayload, GuardOutcome

# The retract reason when the whole-answer pass redacted what the sentence buffer missed.
RESENT_REASON = "answer_redacted"


@traced("guard.INPUT")
async def guard_input(state: AgentState, runtime: Runtime[TurnContext]) -> dict:
    ctx = runtime.context
    outcome = await check(
        ctx,
        state,
        Checkpoint.INPUT,
        {"message": state["user_message"], "attachments": state.get("attachments") or []},
        tools=(),
    )
    if outcome.blocked:
        raise blocked("input_blocked", outcome)
    return {"user_message": outcome.payload["message"], "guard_events": ctx.guard_records()}


@traced("guard.TOOL_LIST")
async def guard_tool_list(state: AgentState, runtime: Runtime[TurnContext]) -> dict:
    """Keeps only routed capabilities the principal holds the permission for
    (defense in depth after routing), then lets policies filter further."""
    ctx = runtime.context
    registry = ctx.services.registry
    permitted = [
        s for s in state["intent"]["capabilities"] if ctx.principal.has(registry.get(s).manifest.permission)
    ]
    outcome = await check(ctx, state, Checkpoint.TOOL_LIST, permitted, tools=())
    if not outcome.payload:
        raise TurnError("no_capability_available", detail="every routed capability was filtered out")
    return {
        "intent": {**state["intent"], "capabilities": list(outcome.payload)},
        "guard_events": ctx.guard_records(),
    }


def _late_block(
    state: AgentState, runtime: Runtime[TurnContext], outcome: GuardOutcome, code: str
) -> TurnError:
    """A blocking verdict on text already streamed (streaming mode): withdraw it first."""
    retract_streamed(state, runtime, code)
    return blocked(code, outcome)


def _resend(state: AgentState, runtime: Runtime[TurnContext], text: str, attribute: str) -> None:
    """Streaming mode: withdraw the released text and stream the redacted text in its place
    (ADR-0039 path, ADR-0111), recorded on the node's span."""
    config = runtime.context.services.guard_config.streaming
    emit(runtime, "retract", {"reason": RESENT_REASON})
    for sentence in split_sentences(text, config.terminators, config.flush_chars):
        emit(runtime, "token", {"text": sentence})
    trace.get_current_span().set_attribute(attribute, "resent")


async def _rewrite(state: AgentState, ctx: TurnContext, outcome: GuardOutcome) -> str:
    """The one rewrite (README §10.1): the answer again, with the violation as a policy hint."""
    policy = next(e for e in outcome.events if f"{e.policy_id}@{e.policy_version}" == outcome.decided_by)
    hint = ctx.services.prompts.render(
        "guardrails.rewrite", state["locale"], policy=policy.policy_id, explanation=policy.detail or ""
    )
    prepared = await prepare_answer(state, ctx, policy_hints=[hint.text])
    result = await call_model(ctx, prepared)
    return result.text


@traced("guard.OUTPUT")
async def guard_output(state: AgentState, runtime: Runtime[TurnContext]) -> dict:
    ctx = runtime.context
    streaming = state.get("answer_mode") != "buffered"
    answer = state["draft_answer"]
    payload = {"answer": answer, "citations": state.get("citations") or []}
    outcome = await check(ctx, state, Checkpoint.OUTPUT, payload)
    if outcome.blocked:
        raise (
            _late_block(state, runtime, outcome, "output_blocked")
            if streaming
            else blocked("output_blocked", outcome)
        )
    if outcome.action == "rewrite" and streaming:
        # Only a mid-turn reload can bring this here; the text already left.
        raise _late_block(state, runtime, outcome, "output_blocked")
    if outcome.action == "redact" and streaming and outcome.payload["answer"] != answer:
        # A span no single sentence held: the released text goes again, redacted (ADR-0111).
        answer = outcome.payload["answer"]
        _resend(state, runtime, answer, "output.final_pass")
        return {"draft_answer": answer, "shown_answer": answer, "guard_events": ctx.guard_records()}
    if outcome.action == "rewrite":
        rewritten = await _rewrite(state, ctx, outcome)
        scripts = ctx.services.validation.scripts_for(state.get("locale") or "en")
        if validation_issues(rewritten, payload["citations"], [], scripts=scripts):
            raise TurnError("validation_failed", detail="the rewritten answer failed validation")
        second = await check(ctx, state, Checkpoint.OUTPUT, {**payload, "answer": rewritten})
        if second.blocked or second.action == "rewrite":
            # At most one rewrite: a second violation withholds the answer.
            raise blocked("output_blocked", second if second.blocked else outcome)
        answer = second.payload["answer"]
    elif outcome.action == "redact":
        answer = outcome.payload["answer"]
    return {"draft_answer": answer, "guard_events": ctx.guard_records()}


@traced("guard.DATA")
async def guard_data(state: AgentState, runtime: Runtime[TurnContext]) -> dict:
    """The whole answer through DATA once. Buffered mode: before release_answer streams it.
    Streaming mode: every sentence already went through DATA (app/graph/streaming.py); this
    pass catches what no sentence held whole and re-sends the text redacted (ADR-0111)."""
    if state.get("answer_mode") != "buffered":
        return await _final_data_pass(state, runtime)
    ctx = runtime.context
    outcome = await check(
        ctx, state, Checkpoint.DATA, DataPayload(text=state["draft_answer"], target="answer")
    )
    if outcome.blocked:
        raise blocked("data_blocked", outcome)
    return {"draft_answer": outcome.payload.text, "guard_events": ctx.guard_records()}


async def _final_data_pass(state: AgentState, runtime: Runtime[TurnContext]) -> dict:
    ctx = runtime.context
    released = state.get("draft_answer") or ""
    span = trace.get_current_span()
    if not released:
        return {}
    outcome = await ctx.services.guard.evaluate(
        Checkpoint.DATA,
        DataPayload(text=released, target="answer"),
        principal=ctx.principal,
        context=guard_context(state, ctx),
    )
    matched = [e for e in outcome.events if e.policy_id]
    if outcome.unavailable:
        ctx.report_guard(matched)
        retract_streamed(state, runtime, "guard_unavailable")
        raise TurnError("guard_unavailable", retryable=True, detail=f"DATA: {outcome.unavailable}")
    if outcome.blocked:
        ctx.report_guard(matched)
        raise _late_block(state, runtime, outcome, "data_blocked")
    if outcome.payload.text == released:
        # Everything matched here was already redacted sentence by sentence: report nothing twice.
        span.set_attribute("data.final_pass", "clean")
        return {}
    ctx.report_guard(matched)
    text = outcome.payload.text
    _resend(state, runtime, text, "data.final_pass")
    return {"draft_answer": text, "shown_answer": text, "guard_events": ctx.guard_records()}


@traced("release_answer")
async def release_answer(state: AgentState, runtime: Runtime[TurnContext]) -> dict:
    """Buffered mode: the checked answer leaves now, a sentence per token event."""
    if state.get("answer_mode") != "buffered":
        return {}
    ctx = runtime.context
    config = ctx.services.guard_config.streaming
    text = state["draft_answer"]
    for sentence in split_sentences(text, config.terminators, config.flush_chars):
        ctx.emit("token", {"text": sentence})
    return {"shown_answer": text}
