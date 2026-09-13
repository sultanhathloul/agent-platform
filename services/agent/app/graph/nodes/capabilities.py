"""run_capabilities (README §5.2, §18.3): invokes each selected capability's
compiled subgraph with a CapabilityInput and a RuntimeContext (ADR-0038), and
collects its CapabilityOutput. Events a capability writes stream to the client
while it runs. A capability sees only its own state namespace (runtime guard,
§18.4)."""

import asyncio
from collections.abc import Callable
from typing import Any

from langgraph.runtime import Runtime

from app.capabilities.contract import CapabilityInput, CapabilityOutput, RuntimeContext
from app.capabilities.registry import LoadedCapability
from app.context.budget import NeedsMore
from app.context.package import AssemblyRequest, ContextPackage
from app.errors import TurnError
from app.graph.deps import TurnContext
from app.graph.nodes import emit, traced
from app.graph.state import AgentState
from app.models.gateway import ModelResult, ModelUnavailable
from app.models.routing import TierDecision
from app.observability.otel import tracer
from app.tools.spec import ToolResult

Writer = Callable[[str, dict[str, Any]], None]


def _request_for(cap: LoadedCapability, state: AgentState, ctx: TurnContext) -> AssemblyRequest:
    manifest = cap.manifest
    locale = state["locale"]
    return AssemblyRequest(
        node=f"capability.{cap.slug}",
        needs=manifest.context.needs,
        # A capability that does not need the system block ships no system prompt (ADR-0020).
        system_prompt=f"{cap.slug}.system" if "system" in manifest.context.needs else None,
        locale=locale,
        state=dict(state),
        tools=[
            spec.description_ar if locale == "ar" else spec.description_en
            for spec in cap.tools
            if ctx.principal.has(spec.required_permission)
        ],
    )


async def _assemble(
    cap: LoadedCapability, request: AssemblyRequest, decision: TierDecision, ctx: TurnContext
) -> ContextPackage:
    """Assembles for the tier the resolver chose, within the manifest's input cap."""
    services = ctx.services
    budget = services.assembler.budget(
        decision.final,
        services.models_config.tiers[decision.final],
        max_input=cap.manifest.context.max_input_tokens,
    )
    package = await services.assembler.assemble(request, budget)
    if isinstance(package, NeedsMore):
        raise TurnError("context_overflow")
    return package


class _Models:
    def __init__(self, cap: LoadedCapability, ctx: TurnContext, request: AssemblyRequest, writer: Writer):
        self._cap, self._ctx, self._request, self._writer = cap, ctx, request, writer

    async def call(
        self, role: str, *, step: str | None = None, request: AssemblyRequest | None = None
    ) -> ModelResult:
        services = self._ctx.services
        decision = await services.resolver.resolve(
            role, step=step, capabilities=[self._cap.model_spec()], principal=self._ctx.principal
        )
        self._writer(
            "tier",
            {"role": decision.role, "step": decision.step, "tier": decision.final, "reason": decision.reason},
        )
        # Assembled for the tier this (role, step) resolved to, so a step override
        # gets a package that fits it and passes the gateway's tier check.
        package = await _assemble(self._cap, request or self._request, decision, self._ctx)
        try:
            result = await services.gateway.call(decision, package)
        except ModelUnavailable as exc:
            raise TurnError("model_unavailable", retryable=True, detail=str(exc)) from exc
        self._ctx.usage.add(result.tokens_in, result.tokens_out)
        return result


class _Broker:
    def __init__(self, ctx: TurnContext, writer: Writer):
        self._ctx, self._writer = ctx, writer

    async def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        return await self._ctx.services.broker.execute(
            name, args, principal=self._ctx.principal, emit=self._writer
        )


@traced("run_capabilities")
async def run_capabilities(state: AgentState, runtime: Runtime[TurnContext]) -> dict:
    ctx = runtime.context
    services = ctx.services
    cap_state = dict(state.get("cap_state") or {})
    outputs: list[dict] = []

    def writer(type_: str, data: dict[str, Any]) -> None:
        emit(runtime, type_, data)

    for slug in state["intent"]["capabilities"]:
        cap = services.registry.get(slug)
        manifest = cap.manifest
        with tracer.start_as_current_span(f"capability.{slug}") as span:
            request = _request_for(cap, state, ctx)
            # The input package goes through the resolver at the capability's floor
            # (no step) and gets that tier's real budget.
            decision = await services.resolver.resolve(
                "reasoning", capabilities=[cap.model_spec()], principal=ctx.principal
            )
            own_state = dict(cap_state.get(manifest.state_namespace) or {})
            cap_input = CapabilityInput(
                message=state["user_message"],
                context=await _assemble(cap, request, decision, ctx),
                principal=ctx.principal,
                cap_state=own_state,
                attachments=list(state.get("attachments") or []),
                locale=state["locale"],
            )
            runtime_context = RuntimeContext(
                slug=slug,
                principal=ctx.principal,
                cap_state=own_state,
                models=_Models(cap, ctx, request, writer),
                broker=_Broker(ctx, writer),
                resolver=services.resolver,
                stream_writer=writer,
            )
            try:
                result = await asyncio.wait_for(
                    cap.graph.ainvoke({"input": cap_input}, context=runtime_context),
                    manifest.limits.timeout_s,
                )
            except TimeoutError as exc:
                raise TurnError("turn_failed", retryable=True, detail=f"capability {slug} timed out") from exc
            output = CapabilityOutput.model_validate(result["output"])
            # Events returned in the output rather than streamed arrive after the capability ends.
            for event in output.events:
                emit(runtime, event.type, event.data)
            cap_state[manifest.state_namespace] = output.cap_state
            ctx.usage.add(output.cost.tokens_in, output.cost.tokens_out, output.cost.cost_usd)
            span.set_attribute("capability.findings_chars", len(output.findings))
            span.set_attribute("capability.sources", len(output.sources))
            outputs.append({"capability": slug, **output.model_dump(mode="json")})
    return {"capability_outputs": outputs, "cap_state": cap_state}
