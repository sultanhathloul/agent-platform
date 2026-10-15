"""The only path to tool execution (README §9, CLAUDE.md rule 6).

Validates arguments, checks the permission, runs PRE_TOOL, executes with
timeout and retries, runs POST_TOOL, normalizes the result, emits
tool_start/tool_end events and one `tool.exec` span.

The handler receives a ToolContext beside its arguments: the scope, built here from the
verified principal and the turn (never from the arguments, S1): the tenant, the user and the
document collections the caller's manifest kinds allow (I1, ADR-0202); and the document backend
the caller's configuration names (C6). A capability may call
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
from app.rag.collections import turn_collections
from app.tools.registry import ToolRegistry
from app.tools.spec import ToolContext, ToolFailure, ToolOutput, ToolResult, ToolScope

Emit = Callable[[str, dict[str, Any]], None]
OnGuard = Callable[[GuardOutcome], None]
OnAudit = Callable[[dict[str, Any]], None]


UNDECLARED = "undeclared_tool"
EXTERNAL_WITHOUT_POLICY = "external_tool_without_policy"
# The owner the MCP tool provider registers its tools under: no capability's scope (ADR-0205).
PROVIDER_OWNER = "mcp"


class ToolBroker:
    def __init__(
        self,
        registry: ToolRegistry,
        guard: GuardEngine,
        declared: Mapping[str, Collection[str]] | None = None,
        collections: Mapping[str, Collection[str]] | None = None,
    ):
        """`declared`: each capability's manifest `tools`, by slug. `collections`: the document
        collection kinds each capability's manifest declares (`documents.collections`)."""
        self.registry = registry
        self.guard = guard
        self.declared = {slug: frozenset(tools) for slug, tools in (declared or {}).items()}
        self.collections = {slug: frozenset(kinds) for slug, kinds in (collections or {}).items()}

    def scope_for(
        self, caller: str, principal: Principal, *, conversation_id: str, selected_kbs: Collection[str] = ()
    ) -> ToolScope:
        """The scope of `caller`'s tool calls this turn (I1): the principal's tenant and user, the
        conversation's session collection and the permitted, selected knowledge bases, kept to the
        kinds the caller's manifest declares."""
        return ToolScope(
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            collections=turn_collections(
                permissions=principal.permissions,
                conversation_id=conversation_id,
                selected_kbs=selected_kbs,
                kinds=self.collections.get(caller, frozenset()),
            ),
        )

    async def execute(
        self,
        name: str,
        args: dict[str, Any],
        *,
        caller: str,
        principal: Principal,
        conversation_id: str = "",
        selected_kbs: Collection[str] = (),
        documents: Any = None,
        emit: Emit | None = None,
        context: GuardContext | None = None,
        on_guard: OnGuard | None = None,
        on_audit: OnAudit | None = None,
    ) -> ToolResult:
        if name not in self.declared.get(caller, ()):
            return self._undeclared(name, caller, on_guard)
        spec = self.registry.get(name)
        owner = self.registry.capability_of(name)
        call_id = uuid.uuid4().hex
        emit = emit or (lambda _type, _data: None)
        started = time.monotonic()
        observed: dict[str, str | int | bool] = {}

        with tracer.start_as_current_span("tool.exec") as span:
            span.set_attribute("tool.name", name)
            span.set_attribute("tool.capability", caller)
            span.set_attribute("tool.call_id", call_id)
            start = {"call_id": call_id, "tool": name, "capability": caller}
            if spec.label is not None:
                start["label"] = spec.label.model_dump()
            emit("tool_start", start)

            base = context or GuardContext()
            # The tool's own scope and its caller's; a capability's tool also its owner's (ADR-0025).
            tools = tuple(dict.fromkeys(t for t in (*base.tools, name, caller, owner) if t != PROVIDER_OWNER))
            scoped = GuardContext(kbs=base.kbs, tools=tools, locale=base.locale, turn=base.turn)
            tool_context = ToolContext(
                scope=self.scope_for(
                    caller, principal, conversation_id=conversation_id, selected_kbs=selected_kbs
                ),
                documents=documents,
            )
            result = await self._run(
                spec, args, principal, scoped, tool_context, on_guard or (lambda _outcome: None), observed
            )
            result.latency_ms = int((time.monotonic() - started) * 1000)

            span.set_attribute("tool.status", result.status)
            span.set_attribute("tool.retries", result.retries)
            if result.error:
                span.set_attribute("tool.error", result.error)
            for key, value in observed.items():
                span.set_attribute(f"tool.{key}", value)
            emit(
                "tool_end",
                {
                    "call_id": call_id,
                    "tool": name,
                    "capability": caller,
                    "status": result.status,
                    "latency_ms": result.latency_ms,
                },
            )
            if on_audit is not None:
                # Content-free (G8): who called what, how it ended, how long, how big; never text.
                on_audit(
                    {
                        "call_id": call_id,
                        "tool": name,
                        "caller": caller,
                        "status": result.status,
                        "error": result.error,
                        "latency_ms": result.latency_ms,
                        "retries": result.retries,
                        **observed,
                    }
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
        observed: dict[str, str | int | bool],
    ) -> ToolResult:
        if spec.required_permission is not None and not principal.has(spec.required_permission):
            return ToolResult(status="denied", error="permission")
        if spec.external and not self._external_policy(spec.name):
            on_guard(
                GuardOutcome(
                    action="block",
                    events=[
                        GuardEventRecord(
                            checkpoint=Checkpoint.PRE_TOOL,
                            action="block",
                            detail=f"{spec.name} is external and no enabled PRE_TOOL policy is scoped to it",
                            reason=EXTERNAL_WITHOUT_POLICY,
                            audit_only=True,
                        )
                    ],
                )
            )
            return ToolResult(status="denied", error=EXTERNAL_WITHOUT_POLICY)
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
            except ToolFailure as failure:  # a content-free code; not retried (I8)
                observed.update(failure.observed)
                error = failure.code
                break
            except Exception as exc:  # a tool failure is data for the capability, not a crash
                error = f"{type(exc).__name__}: {exc}"
        if output is None:
            return ToolResult(status="error", error=error, retries=attempt)
        observed.update(output.observed)

        post = await self.guard.evaluate(Checkpoint.POST_TOOL, output, principal=principal, context=context)
        on_guard(post)
        if post.unavailable:
            return ToolResult(status="denied", error="guard_unavailable", retries=attempt)
        if post.blocked:
            return ToolResult(status="denied", error="policy", retries=attempt)
        final: ToolOutput = post.payload
        return ToolResult(
            status="ok", content=final.content, sources=final.sources, data=final.data, retries=attempt
        )

    def _external_policy(self, name: str) -> bool:
        """Whether an enabled PRE_TOOL policy is scoped to this tool or to its server (I16)."""
        server = name.split(".", 1)[0]
        return bool(self.guard.applicable(Checkpoint.PRE_TOOL, {f"tool:{name}", f"tool:{server}.*"}))
