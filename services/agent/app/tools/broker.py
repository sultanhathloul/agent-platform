"""The only path to tool execution (README §9, CLAUDE.md rule 6).

Validates arguments, checks the permission, runs PRE_TOOL, executes with
timeout and retries, runs POST_TOOL, normalizes the result, emits
tool_start/tool_end events and one `tool.exec` span.

Human-in-the-loop: a PRE_TOOL `require_approval` returns `pending_approval`
today. Phase 08 turns it into a LangGraph interrupt() with the resume path.
"""

import asyncio
import time
import uuid
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from app.guardrails.checkpoints import Checkpoint
from app.guardrails.engine import GuardEngine
from app.identity import Principal
from app.observability.otel import tracer
from app.tools.registry import ToolRegistry
from app.tools.spec import ToolOutput, ToolResult

Emit = Callable[[str, dict[str, Any]], None]


class ToolBroker:
    def __init__(self, registry: ToolRegistry, guard: GuardEngine):
        self.registry = registry
        self.guard = guard

    async def execute(
        self, name: str, args: dict[str, Any], *, principal: Principal, emit: Emit | None = None
    ) -> ToolResult:
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

            result = await self._run(spec, args, principal)
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

    async def _run(self, spec, args: dict[str, Any], principal: Principal) -> ToolResult:
        if not principal.has(spec.required_permission):
            return ToolResult(status="denied", error="permission")
        try:
            parsed = spec.input_schema.model_validate(args)
        except ValidationError as exc:
            return ToolResult(status="error", error=f"invalid arguments: {exc.error_count()} error(s)")

        pre = await self.guard.evaluate(
            Checkpoint.PRE_TOOL, {"tool": spec.name, "args": parsed.model_dump()}, principal=principal
        )
        if pre.blocked:
            return ToolResult(status="denied", error="policy")
        if pre.action == "require_approval":
            return ToolResult(status="pending_approval")

        output: ToolOutput | None = None
        error: str | None = None
        attempt = 0
        for attempt in range(spec.max_retries + 1):  # noqa: B007 (attempt is reported)
            try:
                output = await asyncio.wait_for(spec.handler(parsed), timeout=spec.timeout_s)
                break
            except TimeoutError:
                error = f"timeout after {spec.timeout_s}s"
            except Exception as exc:  # a tool failure is data for the capability, not a crash
                error = f"{type(exc).__name__}: {exc}"
        if output is None:
            return ToolResult(status="error", error=error, retries=attempt)

        post = await self.guard.evaluate(Checkpoint.POST_TOOL, output, principal=principal)
        if post.blocked:
            return ToolResult(status="denied", error="policy", retries=attempt)
        final: ToolOutput = post.payload
        return ToolResult(status="ok", content=final.content, sources=final.sources, retries=attempt)
