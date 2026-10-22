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

A turn has one deadline (config/turn.yaml `deadline_s`; I2, ADR-0255): the graph runs inside
its scope, and every bound below it is cut to what remains (app/resilience/deadline.py). At the
deadline the graph is cancelled with every task it owns, and the turn ends `turn_timeout` through
the same terminal-error path as any other error; so does any error raised once the deadline has
passed (an inner call cut by it). The lease is released by `_locked` as for every turn, so the
next turn of the conversation is not refused. Work that belongs to no caller (a status read,
ADR-0245; a registration, ADR-0254) runs without the turn's deadline and is left running.

Admission (the PR #12 review, decisions 1 and 5; ADR-0262, ADR-0264): the deadline opens when the
request arrives. `admit` takes a turn slot (per user, per tenant, platform-wide: refused at once,
`too_many_turns`, or `service_busy` when the platform is full), then checks ownership and takes the
lock, both within `admission_s` (past it: `service_busy`). The slot and the lease are released
together, once, however the turn ends.

After the answer (decision 2; ADR-0263): the answer runs under min(its ceiling, what remains −
`post_answer_reserve_s`). Once its last token is sent the turn keeps at least that reserve and
never ends `turn_timeout`: what follows (the guards on the whole answer, persistence, the summary,
memory) is bounded step by step inside it, and a cut there ends the turn `done` with what was
answered. Recording the turn's guard matches and its place in the conversation list after the
graph are bounded too (BOOKKEEPING_TIMEOUT_S, decision 7).
"""

import asyncio
import contextlib
import itertools
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
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
from app.platform_settings import effective
from app.resilience import deadline
from app.resilience.limits import Slot

log = logging.getLogger(__name__)

_END = object()

RetractOutcome = Literal["retracted", "forbidden", "not_found", "in_flight", "busy"]
# How long a retract waits for the turn it follows to release the conversation.
RETRACT_LOCK_WAIT_S = 10.0
# The bound on each write after the graph: the turn's guard matches, its place in the list (ADR-0267).
BOOKKEEPING_TIMEOUT_S = 5.0
# A refusal before any stream: its HTTP status at the route, and whether a retry may succeed.
_REFUSALS: dict[str, tuple[int, bool]] = {
    "conversation_forbidden": (403, False),
    "turn_in_flight": (409, True),
    "too_many_turns": (429, True),
    "service_busy": (503, True),
}


class TurnRefused(Exception):
    """A turn refused before it started: by its limits, its owner or its lock (ADR-0262)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code
        self.status, self.retryable = _REFUSALS[code]


@dataclass
class Admission:
    """What an admitted turn holds: its slot, its conversation's lease, and its deadline's end on
    the event loop's clock. Released once, however often `release` is called."""

    lease: TurnLease
    slot: Slot
    ends_at: float
    # The platform settings in force when the turn arrived: it keeps them to its end (ADR-0279).
    settings: effective.Effective | None = None

    async def release(self) -> None:
        try:
            await self.lease.release()
        finally:
            self.slot.release()


@dataclass
class _Progress:
    """The graph's state after its last completed step, and whether the turn's end was moved
    once the answer's last token was sent."""

    final: dict = field(default_factory=dict)
    extended: bool = False


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

    async def admit(self, req: TurnRequest, *, arrived: float | None = None) -> Admission:
        """A slot within the turn limits, ownership and the conversation's lock, within
        `admission_s` of the deadline that opened at `arrived` (now by default). Raises TurnRefused.
        The platform settings current now are the turn's to its end (ADR-0279)."""
        snapshot = self.services.platform.current if self.services.platform is not None else None
        with effective.pin(snapshot):
            turn = effective.turn(self.services.turn)
        ends_at = (deadline.now() if arrived is None else arrived) + turn.deadline_s
        principal = req.principal
        slot = self.services.turn_limits.try_acquire(principal.tenant_id, principal.user_id)
        if isinstance(slot, str):
            raise TurnRefused("service_busy" if slot == "platform" else "too_many_turns")
        try:
            # asyncio.timeout, not a cancel scope: delivered once, so a lock connection the pool
            # takes back after it is not cancelled in turn (ADR-0264).
            async with asyncio.timeout(min(turn.admission_s, ends_at - deadline.now())):
                if not await self.claim(req.conversation_id, principal):
                    raise TurnRefused("conversation_forbidden")
                lease = await self.lock(req.conversation_id)
        except (TimeoutError, LockPoolBusy):
            slot.release()
            raise TurnRefused("service_busy") from None
        except BaseException:
            slot.release()
            raise
        if lease is None:
            slot.release()
            raise TurnRefused("turn_in_flight")
        return Admission(lease=lease, slot=slot, ends_at=ends_at, settings=snapshot)

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
        admission: Admission | None = None,
    ) -> AsyncIterator:
        turn_id = uuid.uuid4().hex
        queue: asyncio.Queue = asyncio.Queue()
        seq = itertools.count()

        def emit(type_: str, data) -> None:
            queue.put_nowait(build_event(type_, data, request_id=request_id, turn_id=turn_id, seq=next(seq)))

        task = asyncio.create_task(self._locked(req, request_id, turn_id, parent, emit, queue, admission))
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

    async def _locked(
        self, req, request_id, turn_id, parent, emit, queue, admission: Admission | None
    ) -> None:
        try:
            if admission is None:
                try:
                    admission = await self.admit(req)
                except TurnRefused as refused:
                    emit("error", self.services.messages.error(refused.code, retryable=refused.retryable))
                    return
            # This task and every task the turn starts read the settings it was admitted with.
            effective.pin_here(admission.settings)
            await self._run(req, request_id, turn_id, parent, emit, admission.ends_at)
        finally:
            try:
                if admission is not None:
                    await admission.release()
            finally:
                # The end of the stream is signalled only once the lock is free (ADR-0109).
                queue.put_nowait(_END)

    async def _run(
        self, req: TurnRequest, request_id: str, turn_id: str, parent, emit, ends_at: float
    ) -> None:
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
            budget: deadline.Budget | None = None
            progress = _Progress()
            try:
                try:
                    with deadline.within(ends_at - deadline.now()) as budget:
                        await self._graph(req, ctx, graph_input, emit, budget, progress)
                except deadline.DeadlineExceeded:
                    if not (ctx.answer_sent and progress.final.get("draft_answer") is not None):
                        self._timed_out(ctx, span, emit)
                        return
                    # The answer was sent: the turn ends `done` with it (ADR-0263).
                    log.warning("turn %s reached its deadline after its answer; ended done", turn_id)
                    span.add_event("turn.deadline_after_answer")
                await self._done(req, ctx, span, emit, progress.final)
            except TurnError as exc:
                if self._cut(ctx, budget):
                    self._timed_out(ctx, span, emit)  # an inner call the deadline cut
                    return
                log.warning("turn %s ended with %s: %s", turn_id, exc.code, exc)
                span.set_status(Status(StatusCode.ERROR, exc.code))
                emit("error", self._error(ctx, exc.code, retryable=exc.retryable, exc=exc))
            except asyncio.CancelledError:
                span.set_status(Status(StatusCode.ERROR, "client_disconnected"))
                raise
            except Exception as exc:
                if self._cut(ctx, budget):
                    self._timed_out(ctx, span, emit)
                    return
                log.exception("turn %s failed", turn_id)
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, "turn_failed"))
                emit("error", self._error(ctx, "turn_failed", retryable=True))
            finally:
                await self._record_guard_events(ctx, req)

    @staticmethod
    def _cut(ctx: TurnContext, budget: deadline.Budget | None) -> bool:
        """An error the deadline caused: raised once it passed, before the answer was sent."""
        return budget is not None and budget.expired and not ctx.answer_sent

    def _timed_out(self, ctx: TurnContext, span, emit) -> None:
        log.warning(
            "turn %s ended with turn_timeout after %.0f s",
            ctx.turn_id,
            effective.turn(self.services.turn).deadline_s,
        )
        span.set_status(Status(StatusCode.ERROR, "turn_timeout"))
        emit("error", self._error(ctx, "turn_timeout", retryable=True))

    async def _graph(
        self,
        req: TurnRequest,
        ctx: TurnContext,
        graph_input: dict,
        emit,
        budget: deadline.Budget,
        progress: _Progress,
    ) -> None:
        """The turn proper, inside its deadline: ownership and the graph, its state kept in
        `progress` after every step. Once the answer's last token is sent, the turn's end moves to
        at least `post_answer_reserve_s` from then (ADR-0263)."""
        principal = req.principal
        caller = Owner(principal.user_id, principal.tenant_id)
        owner = await self.services.owners.bind_or_get(
            req.conversation_id, caller, self._stored_owner(req.conversation_id)
        )
        if owner != caller:
            # Refused before the graph writes its input into the thread (ADR-0050).
            raise TurnError("conversation_forbidden", detail="the conversation belongs to another owner")
        async for mode, chunk in self.services.graph.astream(
            graph_input,
            config=_thread(req.conversation_id),
            context=ctx,
            stream_mode=["custom", "values"],
        ):
            if mode == "custom":
                emit(chunk["type"], chunk["data"])
            else:
                progress.final = chunk
            if ctx.answer_sent and not progress.extended:
                progress.extended = True
                budget.extend(deadline.now() + effective.turn(self.services.turn).post_answer_reserve_s)

    async def _done(self, req: TurnRequest, ctx: TurnContext, span, emit, final: dict) -> None:
        """The terminal `done`, after the conversation's place in its owner's list is recorded."""
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
            state_digest=StateDigest(turn=final["turn"], capabilities=final["intent"]["capabilities"]),
            trace_id=current_trace_id(),
        )
        if self.services.settings.observability_capture_content:
            span.set_attribute("langfuse.trace.output", done.answer)
        await self._record_turn(req.conversation_id, final.get("messages") or [])
        emit("done", done)

    async def _record_turn(self, conversation_id: str, messages: list) -> None:
        """The conversation's place in its owner's list (ADR-0192), before `done`, so a list
        read after the turn holds it. Never fails a turn."""
        question = first_question(messages)
        if question is None:
            return
        try:
            async with asyncio.timeout(BOOKKEEPING_TIMEOUT_S):
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
            # Its own bound (ADR-0267): past it the write is cancelled, and the pool discards the
            # connection it left mid-query.
            async with asyncio.timeout(BOOKKEEPING_TIMEOUT_S):
                await self.services.policies.record_events(
                    ctx.guard_log,
                    tenant_id=ctx.principal.tenant_id,
                    conversation_id=req.conversation_id,
                    turn_id=ctx.turn_id,
                )
        except Exception as exc:  # metrics never fail a turn
            log.warning("guard events not recorded for turn %s: %s", ctx.turn_id, type(exc).__name__)
