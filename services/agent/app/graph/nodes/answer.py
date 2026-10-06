"""answer (README §5.2): the reasoning model synthesizes the capability
outputs into the reply and streams it token by token. The tier floor comes
from the selected capabilities' manifests (§19.3).

An answer that fails a script check named in config/validation.yaml is retried
once, at the configured temperature, on the same context package: `retract`
withdraws the streamed text and the retry streams in its place. `validate` ends
the turn only if the retry fails too (ADR-0061)."""

from langgraph.runtime import Runtime
from opentelemetry import trace

from app.context.package import AssemblyRequest
from app.graph.calls import call_model, prepare_call, tier_event
from app.graph.deps import TurnContext
from app.graph.nodes import emit, traced
from app.graph.state import AgentState
from app.memory.types import MemoryScope
from app.text.script import script_issues

RETRY_REASON = "answer_retry"


@traced("answer")
async def answer(state: AgentState, runtime: Runtime[TurnContext]) -> dict:
    ctx = runtime.context
    services = ctx.services
    locale = state["locale"]
    selected = [services.registry.get(slug) for slug in state["intent"]["capabilities"]]

    node = services.assembler.node("answer")
    max_inputs = [
        c.manifest.context.max_input_tokens for c in selected if c.manifest.context.max_input_tokens
    ]
    request = AssemblyRequest(
        node="answer",
        needs=node.needs,
        system_prompt=node.system_prompt,
        locale=locale,
        state=dict(state),
        # The answer node synthesizes; it selects no tools, so none are listed.
        findings=[
            {"capability": o["capability"], "findings": o["findings"]} for o in state["capability_outputs"]
        ],
        memory_scope=MemoryScope.of(ctx.principal),
    )
    prepared = await prepare_call(
        ctx,
        request,
        role="reasoning",
        step="answer",
        capabilities=selected,
        max_input=max(max_inputs) if max_inputs else None,
    )
    emit(runtime, "tier", tier_event(prepared.decision))

    def stream(text: str) -> None:
        emit(runtime, "token", {"text": text})

    result = await call_model(ctx, prepared, on_token=stream)
    tokens_in, tokens_out = result.tokens_in, result.tokens_out

    span = trace.get_current_span()
    retry = services.validation.retry
    scripts = services.validation.scripts_for(locale)
    issues = script_issues(result.text, scripts) if scripts else []
    retrying = bool(retry.attempts and set(issues) & set(retry.issues))
    span.set_attribute("answer.retry", retrying)
    if retrying:
        span.set_attribute("answer.retry.issues", ",".join(issues))
        emit(runtime, "retract", {"reason": RETRY_REASON})
        result = await call_model(ctx, prepared, on_token=stream, temperature=retry.temperature)
        tokens_in += result.tokens_in
        tokens_out += result.tokens_out
        span.set_attribute("answer.retry.passed", not script_issues(result.text, scripts))

    return {
        "draft_answer": result.text,
        # Every token of the final attempt was streamed, so this is what the user saw (ADR-0039).
        "shown_answer": result.text,
        "tokens_in": (state.get("tokens_in") or 0) + tokens_in,
        "tokens_out": (state.get("tokens_out") or 0) + tokens_out,
    }
