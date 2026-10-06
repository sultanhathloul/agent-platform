"""Graph-level guard checkpoints: INPUT (1), TOOL_LIST (2), OUTPUT (5), DATA (6).
PRE_TOOL/POST_TOOL run in the broker, MODEL_TIER in the tier resolver (README §10.1).

- INPUT: a block ends the turn with the policy's message (`input_blocked`); a
  redaction replaces the user message before any model sees it.
- TOOL_LIST: routed capabilities the principal has no permission for are dropped
  (defense in depth), then each is evaluated under its own `tool:{slug}` scope.
- OUTPUT and DATA on the whole answer run only in buffered mode (D3): there a block
  ends the turn before any text left, a `rewrite` asks the model once more with the
  violation explained, and a redaction changes the text release_answer streams. In
  streaming mode the OUTPUT redact policies and DATA already ran on every sentence
  before it was released (ADR-0106), so the whole-answer OUTPUT check skips redact and
  what applies can only warn or log; if a policy that blocks appears between the answer
  and this node (a hot reload mid-turn), the streamed text is retracted, the one case
  besides `validate` where retract remains (ADR-0039).
"""

from langgraph.runtime import Runtime

from app.errors import TurnError
from app.graph.calls import call_model
from app.graph.deps import TurnContext
from app.graph.guarding import blocked, check
from app.graph.nodes import retract_streamed, traced
from app.graph.nodes.answer import prepare_answer
from app.graph.nodes.validate import validation_issues
from app.graph.state import AgentState
from app.graph.streaming import split_sentences
from app.guardrails.checkpoints import Checkpoint
from app.guardrails.schema import ACTIONS, DataPayload, GuardOutcome

WHOLE_ANSWER_ACTIONS = frozenset(ACTIONS) - {"redact"}


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
    if outcome.action in ("rewrite", "redact") and streaming:
        # Only a mid-turn reload can bring these here; the text already left.
        raise _late_block(state, runtime, outcome, "output_blocked")
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
    """Buffered mode only: in streaming mode every sentence went through DATA before it
    was released (app/graph/streaming.py)."""
    if state.get("answer_mode") != "buffered":
        return {}
    ctx = runtime.context
    outcome = await check(
        ctx, state, Checkpoint.DATA, DataPayload(text=state["draft_answer"], target="answer")
    )
    if outcome.blocked:
        raise blocked("data_blocked", outcome)
    return {"draft_answer": outcome.payload.text, "guard_events": ctx.guard_records()}


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
