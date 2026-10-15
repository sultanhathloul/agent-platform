"""Runs one turn through the main graph and yields typed SSE events.

The graph runs in its own task and feeds a queue, so the `turn` span and its
context live in one task for the whole turn. If the client disconnects, the
SSE generator stops and the task is cancelled.

Before the graph runs, the conversation is bound to its first caller and any
other caller is refused, so nothing of a refused turn reaches the thread
(ADR-0050). load_identity_and_state keeps its own check behind this one.

One turn at a time per conversation (D6): the graph runs while this turn holds
the conversation's advisory lock (app/api/locks.py). The route checks ownership,
then takes the lease before it answers, so a non-owner is refused with 403
`conversation_forbidden` and never holds the lock, and a second turn is refused
with 409 `turn_in_flight` before any stream starts; a caller that streams without
a lease gets the lock here, or an `error` event `turn_in_flight`. The turn task
releases the lease before it signals the end of the stream, and the stream waits
for that task instead of cancelling it; only a consumer that leaves early cancels
the task, whose shielded release still completes (ADR-0109). `retract` takes the
same lock, waiting a few seconds for the turn it follows.

Every guard evaluation of the turn (TurnContext.guard_log) goes out with the
terminal event, done or error, for the audit record, and its matched policies
are stored for the admin's matches per day; a guard block's error carries the
policy's own message (README §10.1).
"""

import asyncio
import contextlib
import itertools
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Literal

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from opentelemetry.context import Context
from opentelemetry.trace import Status, StatusCode

from app.api.events import (
    DoneData,
    ErrorData,
    GuardAuditData,
    GuardData,
    StateDigest,
    ToolAuditData,
    Usage,
    build_event,
)
from app.api.history import first_question
from app.api.locks import LockPoolBusy, TurnLease
from app.api.ownership import Owner, StoredOwner
from app.api.schemas import TurnRequest
from app.errors import TurnError
from app.graph.deps import Services, TurnContext
from app.identity import Principal, hash_user_id
from app.memory.types import MemoryScope
from app.observability.langfuse import set_trace_attributes
from app.observability.otel import current_trace_id, tracer

log = logging.getLogger(__name__)

_END = object()

RetractOutcome = Literal["retracted", "forbidden", "not_found", "in_flight", "busy"]
# How long a retract waits for the turn it follows to release the conversation.
RETRACT_LOCK_WAIT_S = 10.0


def _thread(conversation_id: str) -> dict:
    return {"configurable": {"thread_id": conversation_id}}


class TurnRunner:
    def __init__(self, services: Services):
        self.services = services

    def _stored_owner(self, conversation_id: str) -> StoredOwner:
        """Reads the owner a checkpointed thread records (set by load_identity_and_state)."""

        async def read() -> Owner | None:
            values = (await self.services.graph.aget_state(_thread(conversation_id))).values or {}
            if values.get("user_id") is None:
                return None
            return Owner(user_id=values["user_id"], tenant_id=values["tenant_id"])

        return read

    async def owner_of(self, conversation_id: str) -> Owner | None:
        """The conversation's owner without binding anyone. Endpoints that reach a thread
        outside a turn check it themselves (ADR-0048, ADR-0059)."""
        return await self.services.owners.owner_of(conversation_id, self._stored_owner(conversation_id))

    async def retract(
        self, conversation_id: str, turn_id: str, principal: Principal, reason: str
    ) -> RetractOutcome:
        """Marks the reply `turn_id` persisted as retracted, so Context Assembly skips
        it from now on (ADR-0048), and deletes the memory items extracted from that
        turn, in one transaction with the flag (ADR-0071). Idempotent. The gateway
        calls this when a finished turn's audit record cannot be written and the
        client was told the reply is withdrawn."""
        services = self.services
        config = _thread(conversation_id)
        with tracer.start_as_current_span("turn.retract") as span:
            span.set_attribute("conversation_id", conversation_id)
            span.set_attribute("turn_id", turn_id)
            span.set_attribute("retract.reason", reason)
            owner = await self.owner_of(conversation_id)
            if owner is None:
                return "not_found"
            if owner != Owner(principal.user_id, principal.tenant_id):
                return "forbidden"
            try:
                lease = await services.locks.acquire(conversation_id, wait_s=RETRACT_LOCK_WAIT_S)
            except LockPoolBusy:
                return "busy"
            if lease is None:
                return "in_flight"
            try:
                return await self._retract_locked(config, owner, turn_id, reason, span)
            finally:
                await lease.release()

    async def _retract_locked(
        self, config: dict, owner: Owner, turn_id: str, reason: str, span
    ) -> RetractOutcome:
        """The retract proper, while this caller holds the conversation's lock."""
        services = self.services
        values = (await services.graph.aget_state(config)).values or {}
        reply = next(
            (
                m
                for m in reversed(values.get("messages") or [])
                if m.type == "ai" and m.additional_kwargs.get("turn_id") == turn_id
            ),
            None,
        )
        if reply is None:
            return "not_found"
        async with services.db.connection() as conn, conn.transaction():
            # The flag goes through a checkpointer on this transaction's connection, so
            # the flag and the memory delete commit or roll back together.
            graph = services.graph.copy(update={"checkpointer": AsyncPostgresSaver(conn)})
            kwargs = reply.additional_kwargs
            if not kwargs.get("retracted"):
                marked = reply.model_copy(
                    update={"additional_kwargs": {**kwargs, "retracted": True, "retract_reason": reason}}
                )
                # Same message id: add_messages replaces the reply in place. As the graph's
                # last node, so the thread is left with no pending task.
                await graph.aupdate_state(config, {"messages": [marked]}, as_node="write_memory")
            deleted = await services.memory.store.delete_from_turn(
                MemoryScope(tenant_id=owner.tenant_id, user_id=owner.user_id), turn_id, conn=conn
            )
        span.set_attribute("retract.memory_deleted", deleted)
        return "retracted"

    async def lock(self, conversation_id: str) -> TurnLease | None:
        """The conversation's turn lock, or None while another turn holds it (D6). Raises
        LockPoolBusy when every lock connection is in use (503 `service_busy`)."""
        return await self.services.locks.acquire(conversation_id)

    async def claim(self, conversation_id: str, principal: Principal) -> bool:
        """Binds a new conversation to `principal`, or checks that it owns an existing one,
        before any lock is taken (ADR-0050, ADR-0109)."""
        caller = Owner(principal.user_id, principal.tenant_id)
        owner = await self.services.owners.bind_or_get(
            conversation_id, caller, self._stored_owner(conversation_id)
        )
        return owner == caller

    async def stream(
        self,
        req: TurnRequest,
        request_id: str,
        parent: Context | None = None,
        *,
        lease: TurnLease | None = None,
    ) -> AsyncIterator:
        turn_id = uuid.uuid4().hex
        queue: asyncio.Queue = asyncio.Queue()
        seq = itertools.count()

        def emit(type_: str, data) -> None:
            queue.put_nowait(build_event(type_, data, request_id=request_id, turn_id=turn_id, seq=next(seq)))

        task = asyncio.create_task(self._locked(req, request_id, turn_id, parent, emit, queue, lease))
        ended = False
        try:
            while (item := await queue.get()) is not _END:
                yield item
            ended = True
        finally:
            if not ended and not task.done():
                # The consumer left early (a client disconnect): stop the turn. Its release is
                # shielded, so the cancellation cannot interrupt it (ADR-0109).
                task.cancel()
            # After _END the lease is already released; the task only returns. Never cancelled.
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def _locked(self, req, request_id, turn_id, parent, emit, queue, lease: TurnLease | None) -> None:
        try:
            if lease is None:
                try:
                    lease = await self.lock(req.conversation_id)
                except LockPoolBusy:
                    emit("error", self.services.messages.error("service_busy", retryable=True))
                    return
                if lease is None:
                    emit("error", self.services.messages.error("turn_in_flight", retryable=True))
                    return
            await self._run(req, request_id, turn_id, parent, emit)
        finally:
            try:
                if lease is not None:
                    await lease.release()
            finally:
                # The end of the stream is signalled only once the lock is free (ADR-0109).
                queue.put_nowait(_END)

    async def _run(self, req: TurnRequest, request_id: str, turn_id: str, parent, emit) -> None:
        settings = self.services.settings
        principal = req.principal
        ctx = TurnContext(
            services=self.services,
            principal=principal,
            request_id=request_id,
            turn_id=turn_id,
            conversation_id=req.conversation_id,
        )
        graph_input = {
            "conversation_id": req.conversation_id,
            "user_message": req.message,
            "locale": req.locale,
            "turn_attachment_ids": list(req.attachment_ids),
            # Defense in depth (README §9.4): the gateway filtered already; filter again.
            "selected_kbs": [kb for kb in req.selected_kbs if principal.can_read_kb(kb)],
        }
        with tracer.start_as_current_span("turn", context=parent) as span:
            set_trace_attributes(
                span,
                name="turn",
                session_id=req.conversation_id,
                user_hash=hash_user_id(principal.user_id, settings.user_id_hash_salt),
                tenant_id=principal.tenant_id,
                metadata={"request_id": request_id, "turn_id": turn_id, "locale": req.locale},
            )
            if settings.observability_capture_content:
                span.set_attribute("langfuse.trace.input", req.message)
            try:
                caller = Owner(principal.user_id, principal.tenant_id)
                owner = await self.services.owners.bind_or_get(
                    req.conversation_id, caller, self._stored_owner(req.conversation_id)
                )
                if owner != caller:
                    # Refused before the graph writes its input into the thread (ADR-0050).
                    raise TurnError(
                        "conversation_forbidden", detail="the conversation belongs to another owner"
                    )
                final: dict = {}
                async for mode, chunk in self.services.graph.astream(
                    graph_input,
                    config=_thread(req.conversation_id),
                    context=ctx,
                    stream_mode=["custom", "values"],
                ):
                    if mode == "custom":
                        emit(chunk["type"], chunk["data"])
                    else:
                        final = chunk
                # e.g. memory.write_ms: the synchronous memory write's latency (ADR-0058)
                for name, milliseconds in ctx.timings.items():
                    span.set_attribute(name, milliseconds)
                done = DoneData(
                    answer=final["draft_answer"],
                    citations=final.get("citations") or [],
                    usage=Usage(
                        tokens_in=ctx.usage.tokens_in,
                        tokens_out=ctx.usage.tokens_out,
                        cost_usd=ctx.usage.cost_usd,
                    ),
                    guard_events=self._guard_data(ctx),
                    guard_audit=self._guard_audit(ctx),
                    tool_audit=[ToolAuditData.model_validate(entry) for entry in ctx.tool_audit],
                    state_digest=StateDigest(
                        turn=final["turn"], capabilities=final["intent"]["capabilities"]
                    ),
                    trace_id=current_trace_id(),
                )
                if settings.observability_capture_content:
                    span.set_attribute("langfuse.trace.output", done.answer)
                await self._record_turn(req.conversation_id, final.get("messages") or [])
                emit("done", done)
            except TurnError as exc:
                log.warning("turn %s ended with %s: %s", turn_id, exc.code, exc)
                span.set_status(Status(StatusCode.ERROR, exc.code))
                emit("error", self._error(ctx, exc.code, retryable=exc.retryable, exc=exc))
            except asyncio.CancelledError:
                span.set_status(Status(StatusCode.ERROR, "client_disconnected"))
                raise
            except Exception as exc:
                log.exception("turn %s failed", turn_id)
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, "turn_failed"))
                emit("error", self._error(ctx, "turn_failed", retryable=True))
            finally:
                await self._record_guard_events(ctx, req)

    async def _record_turn(self, conversation_id: str, messages: list) -> None:
        """The conversation's place in its owner's list (ADR-0192), before `done`, so a list
        read after the turn holds it. Never fails a turn."""
        question = first_question(messages)
        if question is None:
            return
        try:
            await self.services.owners.record_turn(conversation_id, question)
        except Exception as exc:
            log.warning("conversation %s not recorded in the list: %s", conversation_id, type(exc).__name__)

    @staticmethod
    def _guard_data(ctx: TurnContext) -> list[GuardData]:
        """The client's view of the turn's evaluations (ADR-0116)."""
        return [
            GuardData.model_validate(record.client_view())
            for record in ctx.guard_log
            if not record.audit_only
        ]

    @staticmethod
    def _guard_audit(ctx: TurnContext) -> list[GuardAuditData]:
        """Every evaluation in full, for the gateway's audit record; the gateway strips it (ADR-0116)."""
        return [
            GuardAuditData.model_validate(record.model_dump(mode="json", exclude={"audit_only"}))
            for record in ctx.guard_log
        ]

    def _error(
        self, ctx: TurnContext, code: str, *, retryable: bool, exc: TurnError | None = None
    ) -> ErrorData:
        """errors.yaml's text, or a blocking policy's own message (README §10.1)."""
        error = self.services.messages.error(code, retryable=retryable)
        update: dict = {
            "guard_events": self._guard_data(ctx),
            "guard_audit": self._guard_audit(ctx),
            "tool_audit": [ToolAuditData.model_validate(entry) for entry in ctx.tool_audit],
        }
        if exc is not None and exc.message_ar and exc.message_en:
            update |= {"message_ar": exc.message_ar, "message_en": exc.message_en, "policy_id": exc.policy_id}
        return error.model_copy(update=update)

    async def _record_guard_events(self, ctx: TurnContext, req: TurnRequest) -> None:
        """Matched policies of this turn, without content, for matches per day (C4)."""
        try:
            await self.services.policies.record_events(
                ctx.guard_log,
                tenant_id=ctx.principal.tenant_id,
                conversation_id=req.conversation_id,
                turn_id=ctx.turn_id,
            )
        except Exception as exc:  # metrics never fail a turn
            log.warning("guard events not recorded for turn %s: %s", ctx.turn_id, type(exc).__name__)
