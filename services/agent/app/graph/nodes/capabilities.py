"""run_capabilities (README §5.2, §18.3): invokes each selected capability's
compiled subgraph with a CapabilityInput and a RuntimeContext, and collects its
CapabilityOutput (ADR-0038, ADR-0047).

A capability sees only what its manifest grants (runtime guard, §18.4): a
context package detached from AgentState, a copy of its own state namespace,
the principal, `call_model`, the broker and an event writer that accepts
CapabilityEvents only. Model usage is recorded once, by `call_model`."""

import asyncio
import copy
from collections.abc import Sequence
from typing import Any

from langgraph.runtime import Runtime
from pydantic import BaseModel, ValidationError

from app.api.events import validate_payload
from app.capabilities.contract import (
    CAPABILITY_EVENT_TYPES,
    CapabilityContractError,
    CapabilityEvent,
    CapabilityInput,
    CapabilityOutput,
    RuntimeContext,
)
from app.capabilities.registry import LoadedCapability
from app.context.budget import NeedsMore
from app.context.package import AssemblyRequest, Block, BlockName, ContextPackage
from app.errors import TurnError
from app.graph.deps import TurnContext
from app.graph.nodes import emit, traced
from app.graph.state import AgentState
from app.models.gateway import ModelResult, ModelUnavailable
from app.models.routing import TierDecision
from app.observability.otel import tracer
from app.tools.spec import ToolResult

# The AgentState keys each block's step reads. A capability's assembly request
# carries only the keys its `context.needs` names; never `cap_state`, identity,
# permissions or other capabilities' outputs.
_STATE_KEYS_BY_BLOCK: dict[BlockName, tuple[str, ...]] = {
    "state_digest": ("selected_kbs", "decisions", "open_threads", "artifacts"),
    "window": ("messages",),
}


def _state_for(needs: Sequence[BlockName], state: AgentState) -> dict[str, Any]:
    view: dict[str, Any] = {"user_message": state["user_message"]}
    for block in needs:
        for key in _STATE_KEYS_BY_BLOCK.get(block, ()):
            view[key] = copy.deepcopy(state.get(key) or [])
    return view


def _request_for(cap: LoadedCapability, state: AgentState, ctx: TurnContext) -> AssemblyRequest:
    manifest = cap.manifest
    locale = state["locale"]
    return AssemblyRequest(
        node=f"capability.{cap.slug}",
        needs=manifest.context.needs,
        # A capability that does not need the system block ships no system prompt (ADR-0020).
        system_prompt=f"{cap.slug}.system" if "system" in manifest.context.needs else None,
        locale=locale,
        state=_state_for(manifest.context.needs, state),
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


def _detached(package: ContextPackage) -> ContextPackage:
    """A copy of the blocks and accounting without the assembly request, so the
    capability holds no reference to AgentState."""
    return ContextPackage.model_validate(package.model_dump())


def _findings(cap: LoadedCapability, extra_blocks: Sequence[Block]) -> list[dict[str, str]]:
    for block in extra_blocks:
        if not isinstance(block, Block) or block.name != "findings":
            name = getattr(block, "name", type(block).__name__)
            raise CapabilityContractError(
                f"capability {cap.slug}: extra_blocks carry findings only, got {name!r}"
            )
    if extra_blocks and "findings" not in cap.manifest.context.needs:
        raise CapabilityContractError(f"capability {cap.slug}: extra_blocks need 'findings' in context.needs")
    return [{"capability": cap.slug, "findings": block.content} for block in extra_blocks]


def _model_caller(
    cap: LoadedCapability, ctx: TurnContext, base: AssemblyRequest, runtime: Runtime[TurnContext]
):
    async def call_model(
        role: str, *, step: str | None = None, extra_blocks: Sequence[Block] = ()
    ) -> ModelResult:
        request = base.model_copy(update={"findings": _findings(cap, extra_blocks)})
        services = ctx.services
        decision = await services.resolver.resolve(
            role, step=step, capabilities=[cap.model_spec()], principal=ctx.principal
        )
        emit(
            runtime,
            "tier",
            {"role": decision.role, "step": decision.step, "tier": decision.final, "reason": decision.reason},
        )
        # Assembled for the tier this (role, step) resolved to, so a step override
        # gets a package that fits it and passes the gateway's tier check.
        package = await _assemble(cap, request, decision, ctx)
        try:
            result = await services.gateway.call(decision, package)
        except ModelUnavailable as exc:
            raise TurnError("model_unavailable", retryable=True, detail=str(exc)) from exc
        ctx.usage.add(result.tokens_in, result.tokens_out)
        return result

    return call_model


def _emit_capability_event(runtime: Runtime[TurnContext], cap: LoadedCapability, event: object) -> None:
    # isinstance and the type set are both checked: model_construct skips validation.
    if not isinstance(event, CapabilityEvent) or event.type not in CAPABILITY_EVENT_TYPES:
        kind = getattr(event, "type", type(event).__name__)
        raise CapabilityContractError(f"capability {cap.slug}: event {kind!r} is not a CapabilityEvent")
    try:
        data = validate_payload(event.type, event.data)
    except ValidationError as exc:
        raise CapabilityContractError(f"capability {cap.slug}: invalid {event.type} payload: {exc}") from exc
    emit(runtime, event.type, data)


def _event_writer(runtime: Runtime[TurnContext], cap: LoadedCapability):
    def stream_writer(event: CapabilityEvent) -> None:
        _emit_capability_event(runtime, cap, event)

    return stream_writer


class _Broker:
    def __init__(self, ctx: TurnContext, runtime: Runtime[TurnContext]):
        self._ctx, self._runtime = ctx, runtime

    async def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        return await self._ctx.services.broker.execute(
            name,
            args,
            principal=self._ctx.principal,
            emit=lambda type_, data: emit(self._runtime, type_, data),
        )


def _output(cap: LoadedCapability, result: object) -> CapabilityOutput:
    raw = result.get("output") if isinstance(result, dict) else None
    if isinstance(raw, BaseModel):
        # Revalidate the fields as set: an instance built with model_construct may lack cap_state.
        raw = dict(vars(raw))
    try:
        return CapabilityOutput.model_validate(raw)
    except ValidationError as exc:
        raise TurnError(
            "turn_failed", detail=f"capability {cap.slug} returned an invalid output: {exc}"
        ) from exc


@traced("run_capabilities")
async def run_capabilities(state: AgentState, runtime: Runtime[TurnContext]) -> dict:
    ctx = runtime.context
    services = ctx.services
    cap_state = dict(state.get("cap_state") or {})
    outputs: list[dict] = []

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
            own_state = cap_state.get(manifest.state_namespace) or {}
            cap_input = CapabilityInput(
                message=state["user_message"],
                context=_detached(await _assemble(cap, request, decision, ctx)),
                principal=ctx.principal,
                cap_state=copy.deepcopy(own_state),
                attachments=copy.deepcopy(list(state.get("attachments") or [])),
                locale=state["locale"],
            )
            runtime_context = RuntimeContext(
                slug=slug,
                principal=ctx.principal,
                cap_state=copy.deepcopy(own_state),
                call_model=_model_caller(cap, ctx, request, runtime),
                broker=_Broker(ctx, runtime),
                stream_writer=_event_writer(runtime, cap),
            )
            try:
                result = await asyncio.wait_for(
                    cap.graph.ainvoke({"input": cap_input}, context=runtime_context),
                    manifest.limits.timeout_s,
                )
            except TimeoutError as exc:
                raise TurnError("turn_failed", retryable=True, detail=f"capability {slug} timed out") from exc
            except CapabilityContractError as exc:
                raise TurnError("turn_failed", detail=str(exc)) from exc
            output = _output(cap, result)
            # Events returned in the output rather than streamed arrive after the capability ends.
            try:
                for event in output.events:
                    _emit_capability_event(runtime, cap, event)
            except CapabilityContractError as exc:
                raise TurnError("turn_failed", detail=str(exc)) from exc
            cap_state[manifest.state_namespace] = output.cap_state
            # Model tokens were recorded by call_model; the output adds non-model cost only.
            ctx.usage.add(0, 0, output.cost.cost_usd)
            span.set_attribute("capability.findings_chars", len(output.findings))
            span.set_attribute("capability.sources", len(output.sources))
            outputs.append({"capability": slug, **output.model_dump(mode="json")})
    return {"capability_outputs": outputs, "cap_state": cap_state}
