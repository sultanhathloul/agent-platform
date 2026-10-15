"""run_capabilities (README §5.2, §18.3): invokes each selected capability's
compiled subgraph with a CapabilityInput and a RuntimeContext, and collects its
CapabilityOutput (ADR-0038, ADR-0047).

A capability sees only what its manifest grants (runtime guard, §18.4): a
context package detached from AgentState, a copy of its own state namespace,
the principal, `call_model`, the broker and an event writer that accepts
CapabilityEvents only. Model usage is recorded once, by `call_model`.

The subgraph runs as a top-level graph with a fresh LangGraph config, never the
main graph's: no checkpointer, thread id, store or callbacks (ADR-0052). It also
runs behind an exception/return firewall: any escape a capability attempts —
`ParentCommand`, `GraphInterrupt`, a returned `Command`, a timeout or any other
exception — is caught at the task boundary and turned into a `capability_failed`
result, so nothing a capability raises reaches the main graph's retry handler or
its checkpoint (ADR-0053). The boundary is data-only: the output crosses as JSON
text serialized inside the task, and a failure as its exception's type name, so
no object the capability created reaches this node (ADR-0054)."""

import asyncio
import contextlib
import contextvars
import copy
import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.runnables.config import var_child_runnable_config
from langgraph.runtime import Runtime
from pydantic import ValidationError
from pydantic_core import to_jsonable_python

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
from app.context.package import AssemblyRequest, Block, BlockName, ContextPackage
from app.errors import TurnError
from app.graph import calls
from app.graph.deps import TurnContext
from app.graph.guarding import guard_context
from app.graph.nodes import emit, traced
from app.graph.state import AgentState
from app.guardrails.schema import GuardOutcome
from app.memory.types import MemoryScope
from app.models.gateway import ModelResult
from app.observability.otel import tracer
from app.tools.spec import ToolResult

log = logging.getLogger(__name__)


class CapabilityFailed(Exception):
    """A capability failed to produce a clean CapabilityOutput: it raised or
    returned an escape (ParentCommand, GraphInterrupt, a Command), timed out,
    compiled its own checkpointer or store, or returned an invalid output. The
    core turns it into the `capability_failed` turn error (ADR-0053). `reason`
    is short and safe to log and put on the span; it never carries capability
    output."""

    def __init__(self, slug: str, reason: str, *, retryable: bool = False):
        super().__init__(f"capability {slug}: {reason}")
        self.slug = slug
        self.reason = reason
        self.retryable = retryable


# The AgentState keys each block's step reads. A capability's assembly request
# carries only the keys its `context.needs` names; never `cap_state`, identity,
# permissions or other capabilities' outputs. The memory block reads no state:
# its scope is the principal, set below (ADR-0047, ADR-0058).
_STATE_KEYS_BY_BLOCK: dict[BlockName, tuple[str, ...]] = {
    "state_digest": ("selected_kbs", "decisions", "open_threads", "artifacts"),
    "summary": ("summary",),
    "window": ("messages", "summary_cursor"),
}


def _state_for(needs: Sequence[BlockName], state: AgentState) -> dict[str, Any]:
    view: dict[str, Any] = {"user_message": state["user_message"]}
    for block in needs:
        for key in _STATE_KEYS_BY_BLOCK.get(block, ()):
            view[key] = copy.deepcopy(state.get(key))
    return view


def _request_for(cap: LoadedCapability, state: AgentState, ctx: TurnContext) -> AssemblyRequest:
    manifest = cap.manifest
    locale = state["locale"]
    needs = manifest.context.needs or []  # None: the answer defaults, but nothing for its own calls
    return AssemblyRequest(
        node=f"capability.{cap.slug}",
        needs=needs,
        # A capability that does not need the system block ships no system prompt (ADR-0020).
        system_prompt=f"{cap.slug}.system" if "system" in needs else None,
        locale=locale,
        state=_state_for(needs, state),
        tools=[
            spec.description_ar if locale == "ar" else spec.description_en
            for spec in cap.tools
            if spec.required_permission is None or ctx.principal.has(spec.required_permission)
        ],
        memory_scope=MemoryScope.of(ctx.principal) if "memory" in needs else None,
    )


async def _prepare(
    cap: LoadedCapability, request: AssemblyRequest, ctx: TurnContext, *, role: str, step: str | None = None
) -> calls.PreparedCall:
    """Assembled for the tier (role, step) resolves to against this manifest, within
    its input cap, on the one model-call path (ADR-0056)."""
    return await calls.prepare_call(
        ctx,
        request,
        role=role,
        step=step,
        capabilities=[cap],
        max_input=cap.manifest.context.max_input_tokens,
    )


def _detached(package: ContextPackage) -> ContextPackage:
    """A copy of the blocks and accounting without the assembly request, so the
    capability holds no reference to AgentState."""
    return ContextPackage.model_validate(package.model_dump())


def _context_reader(cap: LoadedCapability, request: AssemblyRequest, ctx: TurnContext):
    """The capability's package, assembled through the resolver at its floor (no step) on the
    first read only, then a fresh detached copy on every read (owner decision C3)."""
    assembled: list[ContextPackage] = []

    async def context() -> ContextPackage:
        if not assembled:
            prepared = await _prepare(cap, request, ctx, role="reasoning")
            assembled.append(_detached(prepared.package))
        return _detached(assembled[0])

    return context


def _findings(cap: LoadedCapability, extra_blocks: Sequence[Block]) -> list[dict[str, str]]:
    for block in extra_blocks:
        if not isinstance(block, Block) or block.name != "findings":
            name = getattr(block, "name", type(block).__name__)
            raise CapabilityContractError(
                f"capability {cap.slug}: extra_blocks carry findings only, got {name!r}"
            )
    if extra_blocks and "findings" not in (cap.manifest.context.needs or ()):
        raise CapabilityContractError(f"capability {cap.slug}: extra_blocks need 'findings' in context.needs")
    return [{"capability": cap.slug, "findings": block.content} for block in extra_blocks]


def _model_caller(
    cap: LoadedCapability, ctx: TurnContext, base: AssemblyRequest, runtime: Runtime[TurnContext]
):
    async def call_model(
        role: str,
        *,
        step: str | None = None,
        extra_blocks: Sequence[Block] = (),
        prompt: str | None = None,
        prompt_vars: Mapping[str, Any] | None = None,
    ) -> ModelResult:
        request = base.model_copy(update={"findings": _findings(cap, extra_blocks)})
        if prompt is not None:
            # Only the capability's own prompts, as the call's system block (I12).
            if not prompt.startswith(f"{cap.slug}."):
                raise CapabilityContractError(f"capability {cap.slug}: prompt {prompt!r} is not its own")
            ctx.services.prompts.require(prompt)
            needs = ["system", *(n for n in request.needs if n != "system")]
            request = request.model_copy(
                update={"system_prompt": prompt, "needs": needs, "prompt_vars": dict(prompt_vars or {})}
            )
        # Assembled for the tier this (role, step) resolved to, so a step override
        # gets a package that fits it and passes the gateway's tier check.
        prepared = await _prepare(cap, request, ctx, role=role, step=step)
        emit(runtime, "tier", calls.tier_event(prepared.decision))
        return await calls.call_model(ctx, prepared)

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
    def __init__(self, ctx: TurnContext, runtime: Runtime[TurnContext], state: AgentState, slug: str):
        self._ctx, self._runtime, self._state, self._slug = ctx, runtime, state, slug

    async def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        # The scope comes from the verified principal and this turn, never from the capability or
        # a model (S1, I1); the backend is the one the capability's configuration names (C6).
        return await self._ctx.services.broker.execute(
            name,
            args,
            caller=self._slug,
            principal=self._ctx.principal,
            conversation_id=self._state["conversation_id"],
            selected_kbs=tuple(self._state.get("selected_kbs") or ()),
            documents=self._ctx.services.backend_for(self._slug),
            emit=lambda type_, data: emit(self._runtime, type_, data),
            context=guard_context(self._state, self._ctx, tools=()),
            on_guard=self._on_guard,
            on_audit=self._ctx.tool_audit.append,
        )

    def _on_guard(self, outcome: GuardOutcome) -> None:
        self._ctx.report_guard(outcome.events)
        if outcome.unavailable and self._ctx.guard_unavailable is None:
            self._ctx.guard_unavailable = outcome.unavailable


def _output(cap: LoadedCapability, payload: str) -> CapabilityOutput:
    """Re-validates the JSON text that crossed the boundary into a CapabilityOutput
    the core builds. An instance built with model_construct that lacks cap_state
    serialized without it, so it fails here (ADR-0054)."""
    try:
        return CapabilityOutput.model_validate_json(payload)
    except ValidationError as exc:
        raise CapabilityFailed(cap.slug, f"returned an invalid output: {exc}") from exc


def _exception_name(exc: BaseException) -> str:
    """The exception's type name and nothing else: never `str(exc)` or `repr`,
    which run capability code. A metaclass can make even `__name__` raise or
    return a str subclass, so both fall back to a fixed name (ADR-0054)."""
    try:
        name = type(exc).__name__
    except BaseException:
        return "an exception"
    return name if type(name) is str else "an exception"


async def _run_to_json(
    cap: LoadedCapability, cap_input: CapabilityInput, context: RuntimeContext
) -> tuple[bool, str]:
    """The capability's side of the data-only boundary (ADR-0054). Everything that
    touches an object the capability created runs here, inside the isolated task
    and behind the catch-all: the subgraph, reading its output, and serializing it
    with `model_dump(mode="json")`. What leaves is core-built data only:
    `(True, json_text)` or `(False, reason)`. A cancellation is re-raised as a
    fresh CancelledError outside the handler, so not even the exception object
    crosses."""
    try:
        result = await cap.graph.ainvoke({"input": cap_input}, context=context)
        raw = result.get("output") if isinstance(result, dict) else None
        data = raw.model_dump(mode="json") if isinstance(raw, CapabilityOutput) else to_jsonable_python(raw)
        return True, json.dumps(data, ensure_ascii=False)
    except asyncio.CancelledError:
        pass  # re-raised below, outside the handler
    except BaseException as exc:
        # ParentCommand, GraphInterrupt/GraphBubbleUp, a returned Command turned
        # into ParentCommand, an unserializable output, or any other escape.
        return False, f"raised {_exception_name(exc)}"
    raise asyncio.CancelledError


async def _invoke_isolated(cap: LoadedCapability, cap_input: CapabilityInput, context: RuntimeContext) -> str:
    """Runs the capability subgraph outside the main graph's RunnableConfig, behind
    an exception/return firewall and a data-only boundary (ADR-0052, ADR-0053,
    ADR-0054). Returns the capability's output as JSON text.

    Invoked inside a main-graph node, LangGraph would hand the subgraph the main
    graph's config through a context variable: its checkpointer (every
    conversation's thread), thread id, store and callbacks, all reachable with
    `get_config()`. The subgraph runs in a task whose context clears that
    variable, so it starts a config of its own and reaches the core only through
    `context`. The OpenTelemetry context is kept, so spans still nest under
    `capability.{slug}`.

    The task boundary is also a firewall. A capability node can raise a LangGraph
    control-flow signal — `ParentCommand` (also the shape a returned
    `Command(graph=PARENT)` takes), `GraphInterrupt` — or any other exception. If
    that reached the main graph unhandled, its retry handler would apply the
    command to the main graph's state or leave the thread with a pending
    interrupt, both written to the checkpoint. `_run_to_json` catches every such
    escape inside the task and returns data, never the exception or the output
    object: core code here that formatted the exception or inspected the output
    would run the capability's `__str__` or `__class__` in the main node, outside
    the task, where a raised control-flow signal escapes again (ADR-0054).
    `CancelledError` (the turn was cancelled, e.g. a client disconnect) is the one
    exception that must not be swallowed: the subgraph task is cancelled and
    drained first, then it is re-raised so the runner ends the turn as cancelled."""
    graph = cap.graph
    checkpointer = getattr(graph, "checkpointer", None)
    if (checkpointer is not None and checkpointer is not False) or getattr(graph, "store", None) is not None:
        raise CapabilityFailed(cap.slug, "its graph must compile without a checkpointer or store")
    isolated = contextvars.copy_context()
    isolated.run(var_child_runnable_config.set, None)
    task = asyncio.create_task(_run_to_json(cap, cap_input, context), context=isolated)
    try:
        ok, text = await asyncio.wait_for(task, cap.manifest.limits.timeout_s)
    except TimeoutError as exc:
        raise CapabilityFailed(cap.slug, "timed out", retryable=True) from exc
    except asyncio.CancelledError:
        task.cancel()
        with contextlib.suppress(BaseException):
            await task
        raise
    if not ok:
        raise CapabilityFailed(cap.slug, text)
    return text


def _record_failure(span: Any, slug: str, reason: str) -> None:
    """Records a capability failure on its span — the trace channel that carries
    tool and guard events (§12) — as a `capability_failed` event with a ToolCall
    shape, and logs it. The turn then ends per the existing rule (a capability
    that produced no valid output ends the turn), so this is not written to the
    checkpoint: an errored turn persists nothing (ADR-0053)."""
    record = {"tool": f"capability:{slug}", "status": "error", "error": reason}
    span.set_attribute("capability.status", "error")
    span.add_event("capability_failed", record)
    log.warning("capability %s failed: %s", slug, reason)


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
            own_state = cap_state.get(manifest.state_namespace) or {}
            cap_input = CapabilityInput(
                message=state["user_message"],
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
                broker=_Broker(ctx, runtime, state, slug),
                stream_writer=_event_writer(runtime, cap),
                documents=dict(services.backends),
                context=_context_reader(cap, request, ctx),
                config=copy.deepcopy(cap.config),
            )
            try:
                payload = await _invoke_isolated(cap, cap_input, runtime_context)
                output = _output(cap, payload)
                # Events returned in the output rather than streamed arrive after the capability ends.
                for event in output.events:
                    _emit_capability_event(runtime, cap, event)
            except (CapabilityFailed, CapabilityContractError) as exc:
                retryable = isinstance(exc, CapabilityFailed) and exc.retryable
                _record_failure(span, slug, str(exc))
                raise TurnError("capability_failed", retryable=retryable, detail=str(exc)) from exc
            cap_state[manifest.state_namespace] = output.cap_state
            # Model tokens were recorded by call_model; the output adds non-model cost only.
            ctx.usage.add(0, 0, output.cost.cost_usd)
            span.set_attribute("capability.findings_chars", len(output.findings))
            span.set_attribute("capability.sources", len(output.sources))
            outputs.append({"capability": slug, **output.model_dump(mode="json")})
            if ctx.guard_unavailable:
                # A fail-closed tool policy could not be evaluated: the turn ends (D8).
                raise TurnError("guard_unavailable", retryable=True, detail=f"tool: {ctx.guard_unavailable}")
    return {"capability_outputs": outputs, "cap_state": cap_state, "guard_events": ctx.guard_records()}
