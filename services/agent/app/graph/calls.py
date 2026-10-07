"""One path for every model call (CLAUDE.md rule 6; ADR-0017, ADR-0056).

`prepare_call` resolves the tier, assembles for that tier's budget and resolves a
NeedsMore once: one tier up when the overflow action is `escalate` and the cap and
the per-turn escalation limit allow it, otherwise the compress pass (README §7
step 9, §19.3 step 3). A package that still does not fit ends the turn as
`context_overflow`. The package is recorded for the debug endpoint when that is
enabled. `call_model` sends it to LiteLLM through the model gateway and records
usage once."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from opentelemetry import trace

from app.capabilities.registry import LoadedCapability
from app.context.budget import Budget, NeedsMore
from app.context.package import AssemblyRequest, ContextPackage
from app.errors import TurnError
from app.graph.deps import TurnContext
from app.models.gateway import ModelResult, ModelUnavailable
from app.models.routing import TierDecision


@dataclass(frozen=True)
class PreparedCall:
    decision: TierDecision
    package: ContextPackage


def tier_event(decision: TierDecision) -> dict:
    return {"role": decision.role, "step": decision.step, "tier": decision.final, "reason": decision.reason}


def _budget(ctx: TurnContext, decision: TierDecision, max_input: int | None) -> Budget:
    services = ctx.services
    return services.assembler.budget(
        decision.final, services.models_config.tiers[decision.final], max_input=max_input
    )


async def prepare_call(
    ctx: TurnContext,
    request: AssemblyRequest,
    *,
    role: str,
    step: str | None = None,
    capabilities: Sequence[LoadedCapability] = (),
    max_input: int | None = None,
) -> PreparedCall:
    services = ctx.services
    specs = [cap.model_spec() for cap in capabilities]
    decision = await services.resolver.resolve(role, step=step, capabilities=specs, principal=ctx.principal)
    ctx.report_guard(decision.guard_events)
    package = await services.assembler.assemble(request, _budget(ctx, decision, max_input))
    if isinstance(package, NeedsMore):
        action = services.resolver.overflow_action(specs)
        trace.get_current_span().add_event(
            "context.needs_more",
            {"node": request.node, "deficit_tokens": package.deficit_tokens, "action": action},
        )
        escalated = None
        if (
            action == "escalate"
            and ctx.escalations < services.models_config.escalation.max_escalations_per_turn
        ):
            escalated = await services.resolver.escalate(
                decision, principal=ctx.principal, reason="context_overflow"
            )
        if escalated is not None:
            ctx.report_guard(escalated.guard_events)
        if escalated is not None and escalated.final != decision.final:
            ctx.escalations += 1
            decision = escalated
            package = await services.assembler.assemble(request, _budget(ctx, decision, max_input))
        else:
            compressed = request.model_copy(update={"compress": True})
            package = await services.assembler.assemble(compressed, _budget(ctx, decision, max_input))
        if isinstance(package, NeedsMore):
            raise TurnError(
                "context_overflow", detail=f"{request.node}: {package.deficit_tokens} tokens over the budget"
            )
    if not request.content_free:  # D5: memory and summary text is never recorded (ADR-0110)
        await services.context_log.record(
            turn_id=ctx.turn_id, conversation_id=ctx.conversation_id, package=package
        )
    return PreparedCall(decision, package)


async def call_model(
    ctx: TurnContext,
    prepared: PreparedCall,
    *,
    on_token: Callable[[str], None] | None = None,
    temperature: float | None = None,
) -> ModelResult:
    try:
        result = await ctx.services.gateway.call(
            prepared.decision, prepared.package, on_token=on_token, temperature=temperature
        )
    except ModelUnavailable as exc:
        raise TurnError("model_unavailable", retryable=True, detail=str(exc)) from exc
    ctx.usage.add(result.tokens_in, result.tokens_out)
    return result
