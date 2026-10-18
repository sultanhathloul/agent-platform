"""The only path to tool execution (README §9, CLAUDE.md rule 6).

Validates arguments, checks the permission, runs PRE_TOOL, executes with
timeout and retries, runs POST_TOOL, normalizes the result, emits
tool_start/tool_end events and one `tool.exec` span.

The handler receives a ToolContext beside its arguments: the scope, built here from the
verified principal and the turn (never from the arguments, S1): the tenant, the user and the
document collections the caller's manifest kinds allow (I1, ADR-0202); and, for a caller whose
manifest declares `documents.collections`, a document facade bound to that scope (search, fetch,
status; ADR-0231), never a backend or a connection. A capability may call
only the tools its manifest declares (`tools`); any other call is refused before anything
runs, with an audit-only record of who called what (ADR-0160).

Both checkpoints evaluate under the scopes `tool:{name}` and `tool:{capability}`
(ADR-0025) besides the turn's own. PRE_TOOL: a block denies the call; a
`require_approval` ends it with status `pending_approval` and error
`approval_required` (phase 08 turns that into an interrupt with a resume path).
POST_TOOL runs sanitize_untrusted first, always (README §10.3), then the policies;
a redaction changes the content the capability receives. A fail-closed policy that
cannot be evaluated denies the call with `guard_unavailable`, and `on_guard` lets the
turn end with that code (D8). A failure is reported by a content-free code only, never text
from a server or an exception (G8, ADR-0234).

`transport` is the one path for a document backend adapter's calls to its MCP server (ADR-0230):
the allowlist (`class: transport` only), the pinned schema, the injected fields, auth, timeout,
byte cap and breaker of app/mcp, and the same content-free span and audit entry as a tool call
(server, tool, status, latency, byte sizes, argument HMAC). Inside a turn the entry joins the
turn's tool audit (the caller set by `auditing`); outside one (an upload's registration, a status
read from the API) it is written to the `audit.transport` log.
"""

import asyncio
import contextlib
import contextvars
import json
import logging
import time
import uuid
from collections.abc import Callable, Collection, Iterator, Mapping
from typing import Any

from pydantic import ValidationError

from app.guardrails.checkpoints import Checkpoint
from app.guardrails.engine import GuardEngine
from app.guardrails.schema import GuardContext, GuardEventRecord, GuardOutcome
from app.identity import Principal
from app.mcp.tools import INVALID_ARGUMENTS, call_transport, transport_servers
from app.observability.otel import tracer
from app.rag.access import DocumentAccess
from app.rag.collections import turn_collections
from app.tools.registry import ToolRegistry
from app.tools.spec import PROVIDER_OWNER, ToolContext, ToolFailure, ToolOutput, ToolResult, ToolScope

Emit = Callable[[str, dict[str, Any]], None]
OnGuard = Callable[[GuardOutcome], None]
OnAudit = Callable[[dict[str, Any]], None]


UNDECLARED = "undeclared_tool"
EXTERNAL_WITHOUT_POLICY = "external_tool_without_policy"
TOOL_TIMEOUT = "tool_timeout"
TOOL_FAILED = "tool_failed"
# The caller named on a transport call outside any turn or tool call.
TRANSPORT_CALLER = "transport"
# What an audit entry carries of a call's observed facts: the audit record's own fields (G8).
AUDIT_OBSERVED = ("server", "request_bytes", "response_bytes", "args_hmac")


def _audited(observed: Mapping[str, str | int | bool]) -> dict[str, str | int | bool]:
    return {key: observed[key] for key in AUDIT_OBSERVED if key in observed}

audit_log = logging.getLogger("audit.transport")
# Who a transport call is for and where its audit entry goes: set around a tool's handler by the
# broker, and by the core around its own document reads inside a turn (evidence, statuses).
_AUDIT: contextvars.ContextVar[tuple[str, OnAudit] | None] = contextvars.ContextVar(
    "transport_audit", default=None
)


@contextlib.contextmanager
def auditing(caller: str, on_audit: OnAudit | None) -> Iterator[None]:
    """Transport calls made inside this block are audited for `caller` through `on_audit`."""
    token = _AUDIT.set((caller, on_audit) if on_audit is not None else None)
    try:
        yield
    finally:
        _AUDIT.reset(token)


def _log_audit(entry: dict[str, Any]) -> None:
    audit_log.info(json.dumps(entry, sort_keys=True))


class ToolBroker:
    def __init__(
        self,
        registry: ToolRegistry,
        guard: GuardEngine,
        declared: Mapping[str, Collection[str]] | None = None,
        collections: Mapping[str, Collection[str]] | None = None,
        *,
        documents: DocumentAccess | None = None,
        mcp: Any = None,
    ):
        """`declared`: each capability's manifest `tools`, by slug. `collections`: the document
        collection kinds each capability's manifest declares (`documents.collections`).
        `documents`: the backends a tool's facade reaches (ADR-0231). `mcp`: the platform's MCP
        client (app.mcp.client.MCPClient), held here and in app/mcp only (ADR-0230)."""
        self.registry = registry
        self.guard = guard
        self.declared = {slug: frozenset(tools) for slug, tools in (declared or {}).items()}
        self.collections = {slug: frozenset(kinds) for slug, kinds in (collections or {}).items()}
        self.documents = documents
        self.mcp = mcp

    def with_documents(self, documents: DocumentAccess) -> "ToolBroker":
        """This broker with other document backends (tests swap the backend)."""
        return ToolBroker(
            self.registry, self.guard, self.declared, self.collections, documents=documents, mcp=self.mcp
        )

    def transport_servers(self) -> set[str]:
        """The MCP servers a backend adapter may reach through `transport`."""
        return transport_servers(self.mcp) if self.mcp is not None else set()

    def mcp_health(self) -> list[dict[str, Any]]:
        return self.mcp.health() if self.mcp is not None else []

    def facade_for(self, caller: str, scope: ToolScope):
        """The scope-bound document facade of `caller`'s tools; None when its manifest declares no
        collection kind (ADR-0231)."""
        if not self.collections.get(caller) or self.documents is None:
            return None
        return self.documents.facade(caller, scope)

    async def transport(self, server: str, tool: str, args: dict[str, Any], scope: Any) -> dict[str, Any]:
        """One transport operation of a backend adapter (ADR-0230), `scope` a DocumentScope or
        ToolScope the core built. Returns the structured result; raises ToolFailure with a
        content-free code. One `tool.exec` span and one audit entry per call."""
        caller, on_audit = _AUDIT.get() or (TRANSPORT_CALLER, _log_audit)
        call_id = uuid.uuid4().hex
        observed: dict[str, str | int | bool] = {"server": server}
        name = f"{server}.{tool}"
        started = time.monotonic()
        status, error = "ok", None
        with tracer.start_as_current_span("tool.exec") as span:
            span.set_attribute("tool.name", name)
            span.set_attribute("tool.capability", caller)
            span.set_attribute("tool.call_id", call_id)
            span.set_attribute("tool.transport", True)
            try:
                if self.mcp is None:
                    raise ToolFailure("tool_unavailable")
                return await call_transport(self.mcp, server, tool, args, scope, observed)
            except ToolFailure as failure:
                status, error = "error", failure.code
                observed.update(failure.observed)
                raise
            except asyncio.CancelledError:
                status, error = "error", "cancelled"
                raise
            except Exception:  # any other failure is a code, never its text (G8)
                status, error = "error", TOOL_FAILED
                raise ToolFailure(TOOL_FAILED) from None
            finally:
                latency = int((time.monotonic() - started) * 1000)
                span.set_attribute("tool.status", status)
                span.set_attribute("tool.latency_ms", latency)
                if error:
                    span.set_attribute("tool.error", error)
                for key, value in observed.items():
                    span.set_attribute(f"tool.{key}", value)
                on_audit(
                    {
                        "call_id": call_id,
                        "tool": name,
                        "caller": caller,
                        "status": status,
                        "error": error,
                        "latency_ms": latency,
                        "retries": 0,
                        **_audited(observed),
                    }
                )

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
            scope = self.scope_for(
                caller, principal, conversation_id=conversation_id, selected_kbs=selected_kbs
            )
            tool_context = ToolContext(scope=scope, documents=self.facade_for(caller, scope))
            with auditing(caller, on_audit):
                result = await self._run(
                    spec, args, principal, scoped, tool_context, on_guard or (lambda _outcome: None), observed
                )
            result.latency_ms = int((time.monotonic() - started) * 1000)

            span.set_attribute("tool.status", result.status)
            span.set_attribute("tool.latency_ms", result.latency_ms)
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
                        **_audited(observed),
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
        except ValidationError:
            return ToolResult(status="error", error=INVALID_ARGUMENTS)

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
                error = TOOL_TIMEOUT
            except ToolFailure as failure:  # a content-free code; not retried (I8)
                observed.update(failure.observed)
                error = failure.code
                break
            except Exception:  # a tool failure is data for the capability, not a crash; its text
                # may come from a server or a document, so only a code is kept (G8, ADR-0234)
                error = TOOL_FAILED
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
