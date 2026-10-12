"""The only path to tool execution (README §9, CLAUDE.md rule 6).

Validates arguments, checks the permission, runs PRE_TOOL, executes with
timeout and retries, runs POST_TOOL, normalizes the result, emits
tool_start/tool_end events and one `tool.exec` span.

The handler receives a ToolContext beside its arguments: the scope, built here from the
verified principal and the turn's conversation (never from the arguments, S1), and the
document backend of the calling capability's RuntimeContext (C6). A capability may call
only the tools its manifest declares (`tools`); any other call is refused before anything
runs, with an audit-only record of who called what (ADR-0160).

Both checkpoints evaluate under the scopes `tool:{name}` and `tool:{capability}`
(ADR-0025) besides the turn's own. PRE_TOOL: a block denies the call; a
`require_approval` ends it with status `pending_approval` and error
`approval_required` (phase 08 turns that into an interrupt with a resume path).
POST_TOOL runs sanitize_untrusted first, always (README §10.3), then the policies;
a redaction changes the content the capability receives. A fail-closed policy that
cannot be evaluated denies the call with `guard_unavailable`, and `on_guard` lets the
turn end with that code (D8).
"""

import asyncio
import time
import uuid
from collections.abc import Callable, Collection, Mapping
from typing import Any

from pydantic import ValidationError

from app.guardrails.checkpoints import Checkpoint
from app.guardrails.engine import GuardEngine
from app.guardrails.schema import GuardContext, GuardEventRecord, GuardOutcome
from app.identity import Principal
from app.observability.otel import tracer
from app.tools.registry import ToolRegistry
from app.tools.spec import ToolContext, ToolOutput, ToolResult, ToolScope

Emit = Callable[[str, dict[str, Any]], None]
OnGuard = Callable[[GuardOutcome], None]


UNDECLARED = "undeclared_tool"


class ToolBroker:
    def __init__(
        self,
        registry: ToolRegistry,
        guard: GuardEngine,
        declared: Mapping[str, Collection[str]] | None = None,
    ):
        """`declared`: each capability's manifest `tools`, by slug."""
        self.registry = registry
        self.guard = guard
        self.declared = {slug: frozenset(tools) for slug, tools in (declared or {}).items()}

    async def execute(
        self,
        name: str,
        args: dict[str, Any],
        *,
        caller: str,
        principal: Principal,
        conversation_id: str = "",
        documents: Any = None,
        emit: Emit | None = None,
        context: GuardContext | None = None,
        on_guard: OnGuard | None = None,
    ) -> ToolResult:
        if name not in self.declared.get(caller, ()):
            return self._undeclared(name, caller, on_guard)
        spec = self.registry.get(name)
        capability = self.registry.capability_of(name)
        call_id = uuid.uuid4().hex
        emit = emit or (lambda _type, _data: None)
        started = time.monotonic()

        with tracer.start_as_current_span("tool.exec") as span:
            span.set_attribute("tool.name", name)
            span.set_attribute("tool.capability", capability)
            span.set_attribute("tool.call_id", call_id)
            emit("tool_start", {"call_id": call_id, "tool": name, "capability": capability})

            base = context or GuardContext()
            scoped = GuardContext(
                kbs=base.kbs, tools=(*base.tools, name, capability), locale=base.locale, turn=base.turn
            )
            tool_context = ToolContext(
                scope=ToolScope(
                    tenant_id=principal.tenant_id, user_id=principal.user_id, conversation_id=conversation_id
                ),
                documents=documents,
            )
            result = await self._run(
                spec, args, principal, scoped, tool_context, on_guard or (lambda _outcome: None)
            )
            result.latency_ms = int((time.monotonic() - started) * 1000)

            span.set_attribute("tool.status", result.status)
            span.set_attribute("tool.retries", result.retries)
            emit(
                "tool_end",
                {
                    "call_id": call_id,
                    "tool": name,
                    "capability": capability,
                    "status": result.status,
                    "latency_ms": result.latency_ms,
                },
            )
            return result

    def _undeclared(self, name: str, caller: str, on_guard: OnGuard | None) -> ToolResult:
        """A call to a tool the caller's manifest does not declare: nothing runs, nothing
        streams, and the audit record names the caller and the tool (ADR-0160)."""
        with tracer.start_as_current_span("tool.exec") as span:
            span.set_attribute("tool.name", name)
            span.set_attribute("tool.caller", caller)
            span.set_attribute("tool.status", "denied")
            span.set_attribute("tool.error", UNDECLARED)
        record = GuardEventRecord(
            checkpoint=Checkpoint.PRE_TOOL,
            action="block",
            detail=f"{caller} called {name}, which its manifest does not declare",
            reason=UNDECLARED,
            audit_only=True,
        )
        if on_guard is not None:
            on_guard(GuardOutcome(action="block", events=[record]))
        return ToolResult(status="denied", error=UNDECLARED)

    async def _run(
        self,
        spec,
        args: dict[str, Any],
        principal: Principal,
        context: GuardContext,
        tool_context: ToolContext,
        on_guard: OnGuard,
    ) -> ToolResult:
        if not principal.has(spec.required_permission):
            return ToolResult(status="denied", error="permission")
        try:
            parsed = spec.input_schema.model_validate(args)
        except ValidationError as exc:
            return ToolResult(status="error", error=f"invalid arguments: {exc.error_count()} error(s)")

        pre = await self.guard.evaluate(
            Checkpoint.PRE_TOOL,
            {"tool": spec.name, "args": parsed.model_dump(mode="json")},
            principal=principal,
            context=context,
        )
        on_guard(pre)
        if pre.unavailable:
            return ToolResult(status="denied", error="guard_unavailable")
        if pre.blocked:
            return ToolResult(status="denied", error="policy")
        if pre.action == "require_approval":
            return ToolResult(status="pending_approval", error="approval_required")

        output: ToolOutput | None = None
        error: str | None = None
        attempt = 0
        for attempt in range(spec.max_retries + 1):  # noqa: B007 (attempt is reported)
            try:
                output = await asyncio.wait_for(spec.handler(parsed, tool_context), timeout=spec.timeout_s)
                break
            except TimeoutError:
                error = f"timeout after {spec.timeout_s}s"
            except Exception as exc:  # a tool failure is data for the capability, not a crash
                error = f"{type(exc).__name__}: {exc}"
        if output is None:
            return ToolResult(status="error", error=error, retries=attempt)

        post = await self.guard.evaluate(Checkpoint.POST_TOOL, output, principal=principal, context=context)
        on_guard(post)
        if post.unavailable:
            return ToolResult(status="denied", error="guard_unavailable", retries=attempt)
        if post.blocked:
            return ToolResult(status="denied", error="policy", retries=attempt)
        final: ToolOutput = post.payload
        return ToolResult(status="ok", content=final.content, sources=final.sources, retries=attempt)
