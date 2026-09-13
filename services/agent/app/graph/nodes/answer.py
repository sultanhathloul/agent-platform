"""answer (README §5.2): the reasoning model synthesizes the capability
outputs into the reply and streams it token by token. The tier floor comes
from the selected capabilities' manifests (§19.3)."""

from langgraph.runtime import Runtime

from app.context.budget import NeedsMore
from app.context.package import AssemblyRequest
from app.errors import TurnError
from app.graph.deps import TurnContext
from app.graph.nodes import emit, traced
from app.graph.state import AgentState
from app.models.gateway import ModelUnavailable


@traced("answer")
async def answer(state: AgentState, runtime: Runtime[TurnContext]) -> dict:
    ctx = runtime.context
    services = ctx.services
    locale = state["locale"]
    selected = [services.registry.get(slug) for slug in state["intent"]["capabilities"]]

    decision = await services.resolver.resolve(
        "reasoning", step="answer", capabilities=[c.model_spec() for c in selected], principal=ctx.principal
    )
    emit(
        runtime,
        "tier",
        {"role": decision.role, "step": decision.step, "tier": decision.final, "reason": decision.reason},
    )

    node = services.assembler.node("answer")
    max_inputs = [
        c.manifest.context.max_input_tokens for c in selected if c.manifest.context.max_input_tokens
    ]
    budget = services.assembler.budget(
        decision.final,
        services.models_config.tiers[decision.final],
        max_input=max(max_inputs) if max_inputs else None,
    )
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
    )
    package = await services.assembler.assemble(request, budget)
    if isinstance(package, NeedsMore):
        # Phase 02: the tier resolver decides compress vs escalate here.
        raise TurnError("context_overflow")

    try:
        result = await services.gateway.call(
            decision, package, on_token=lambda text: emit(runtime, "token", {"text": text})
        )
    except ModelUnavailable as exc:
        raise TurnError("model_unavailable", retryable=True, detail=str(exc)) from exc

    ctx.usage.add(result.tokens_in, result.tokens_out)
    return {
        "draft_answer": result.text,
        # Every token was streamed, so this is what the user saw (ADR-0039).
        "shown_answer": result.text,
        "tokens_in": (state.get("tokens_in") or 0) + result.tokens_in,
        "tokens_out": (state.get("tokens_out") or 0) + result.tokens_out,
    }
