"""run_capabilities (README §5.2, §18.3): invokes each selected capability's
compiled subgraph with a CapabilityInput and collects its CapabilityOutput.
A capability sees only its own state namespace (runtime guard, §18.4)."""

import asyncio

from langgraph.runtime import Runtime

from app.capabilities.contract import CapabilityInput, CapabilityOutput
from app.capabilities.registry import LoadedCapability
from app.context.budget import NeedsMore
from app.context.package import AssemblyRequest, ContextPackage
from app.errors import TurnError
from app.graph.deps import TurnContext
from app.graph.nodes import emit, traced
from app.graph.state import AgentState
from app.observability.otel import tracer


async def _context_for(cap: LoadedCapability, state: AgentState, ctx: TurnContext) -> ContextPackage:
    manifest = cap.manifest
    locale = state["locale"]
    if not manifest.context.needs:
        return ContextPackage(
            node=f"capability.{cap.slug}", tier=manifest.model.min_tier, locale=locale, budget_limit=0
        )
    services = ctx.services
    decision = await services.resolver.resolve(
        "reasoning", capabilities=[cap.model_spec()], principal=ctx.principal
    )
    budget = services.assembler.budget(
        decision.final,
        services.models_config.tiers[decision.final],
        max_input=manifest.context.max_input_tokens,
    )
    request = AssemblyRequest(
        node=f"capability.{cap.slug}",
        needs=manifest.context.needs,
        system_prompt=f"{cap.slug}.system",
        locale=locale,
        state=dict(state),
        tools=[
            spec.description_ar if locale == "ar" else spec.description_en
            for spec in cap.tools
            if ctx.principal.has(spec.required_permission)
        ],
    )
    package = await services.assembler.assemble(request, budget)
    if isinstance(package, NeedsMore):
        raise TurnError("context_overflow")
    return package


@traced("run_capabilities")
async def run_capabilities(state: AgentState, runtime: Runtime[TurnContext]) -> dict:
    ctx = runtime.context
    cap_state = dict(state.get("cap_state") or {})
    outputs: list[dict] = []
    for slug in state["intent"]["capabilities"]:
        cap = ctx.services.registry.get(slug)
        manifest = cap.manifest
        with tracer.start_as_current_span(f"capability.{slug}") as span:
            cap_input = CapabilityInput(
                message=state["user_message"],
                context=await _context_for(cap, state, ctx),
                principal=ctx.principal,
                cap_state=dict(cap_state.get(manifest.state_namespace) or {}),
                attachments=list(state.get("attachments") or []),
                locale=state["locale"],
            )
            try:
                result = await asyncio.wait_for(
                    cap.graph.ainvoke({"input": cap_input}), manifest.limits.timeout_s
                )
            except TimeoutError as exc:
                raise TurnError("turn_failed", retryable=True, detail=f"capability {slug} timed out") from exc
            output = CapabilityOutput.model_validate(result["output"])
            for event in output.events:
                emit(runtime, event.type, event.data)
            cap_state[manifest.state_namespace] = output.cap_state
            ctx.usage.add(output.cost.tokens_in, output.cost.tokens_out, output.cost.cost_usd)
            span.set_attribute("capability.findings_chars", len(output.findings))
            span.set_attribute("capability.sources", len(output.sources))
            outputs.append({"capability": slug, **output.model_dump(mode="json")})
    return {"capability_outputs": outputs, "cap_state": cap_state}
