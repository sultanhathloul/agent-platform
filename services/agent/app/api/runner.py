"""Runs one turn through the main graph and yields typed SSE events.

The graph runs in its own task and feeds a queue, so the `turn` span and its
context live in one task for the whole turn. If the client disconnects, the
SSE generator stops and the task is cancelled.

Before the graph runs, the conversation is bound to its first caller and any
other caller is refused, so nothing of a refused turn reaches the thread
(ADR-0050). load_identity_and_state keeps its own check behind this one.
"""

import asyncio
import itertools
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Literal

from opentelemetry.context import Context
from opentelemetry.trace import Status, StatusCode

from app.api.events import DoneData, StateDigest, Usage, build_event
from app.api.ownership import Owner, StoredOwner
from app.api.schemas import TurnRequest
from app.errors import TurnError
from app.graph.deps import Services, TurnContext
from app.identity import Principal, hash_user_id
from app.observability.langfuse import set_trace_attributes
from app.observability.otel import current_trace_id, tracer

log = logging.getLogger(__name__)

_END = object()

RetractOutcome = Literal["retracted", "forbidden", "not_found"]


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

    async def retract(
        self, conversation_id: str, turn_id: str, principal: Principal, reason: str
    ) -> RetractOutcome:
        """Marks the reply `turn_id` persisted as retracted, so Context Assembly skips
        it from now on (ADR-0048). Idempotent. The gateway calls this when a finished
        turn's audit record cannot be written and the client was told the reply is
        withdrawn."""
        graph = self.services.graph
        config = _thread(conversation_id)
        with tracer.start_as_current_span("turn.retract") as span:
            span.set_attribute("conversation_id", conversation_id)
            span.set_attribute("turn_id", turn_id)
            span.set_attribute("retract.reason", reason)
            owner = await self.services.owners.owner_of(conversation_id, self._stored_owner(conversation_id))
            if owner is None:
                return "not_found"
            if owner != Owner(principal.user_id, principal.tenant_id):
                return "forbidden"
            values = (await graph.aget_state(config)).values or {}
            for message in reversed(values.get("messages") or []):
                kwargs = message.additional_kwargs
                if message.type != "ai" or kwargs.get("turn_id") != turn_id:
                    continue
                if not kwargs.get("retracted"):
                    marked = message.model_copy(
                        update={"additional_kwargs": {**kwargs, "retracted": True, "retract_reason": reason}}
                    )
                    # Same message id: add_messages replaces the reply in place.
                    await graph.aupdate_state(config, {"messages": [marked]}, as_node="persist_state")
                return "retracted"
            return "not_found"

    async def stream(self, req: TurnRequest, request_id: str, parent: Context | None = None) -> AsyncIterator:
        turn_id = uuid.uuid4().hex
        queue: asyncio.Queue = asyncio.Queue()
        seq = itertools.count()

        def emit(type_: str, data) -> None:
            queue.put_nowait(build_event(type_, data, request_id=request_id, turn_id=turn_id, seq=next(seq)))

        task = asyncio.create_task(self._run(req, request_id, turn_id, parent, emit, queue))
        try:
            while (item := await queue.get()) is not _END:
                yield item
        finally:
            if not task.done():
                task.cancel()

    async def _run(self, req: TurnRequest, request_id: str, turn_id: str, parent, emit, queue) -> None:
        settings = self.services.settings
        principal = req.principal
        ctx = TurnContext(services=self.services, principal=principal, request_id=request_id, turn_id=turn_id)
        graph_input = {
            "conversation_id": req.conversation_id,
            "user_message": req.message,
            "locale": req.locale,
            "attachments": [a.model_dump() for a in req.attachments],
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
                done = DoneData(
                    answer=final["draft_answer"],
                    citations=final.get("citations") or [],
                    usage=Usage(
                        tokens_in=ctx.usage.tokens_in,
                        tokens_out=ctx.usage.tokens_out,
                        cost_usd=ctx.usage.cost_usd,
                    ),
                    guard_events=final.get("guard_events") or [],
                    state_digest=StateDigest(
                        turn=final["turn"], capabilities=final["intent"]["capabilities"]
                    ),
                    trace_id=current_trace_id(),
                )
                if settings.observability_capture_content:
                    span.set_attribute("langfuse.trace.output", done.answer)
                emit("done", done)
            except TurnError as exc:
                log.warning("turn %s ended with %s: %s", turn_id, exc.code, exc)
                span.set_status(Status(StatusCode.ERROR, exc.code))
                emit("error", self.services.messages.error(exc.code, retryable=exc.retryable))
            except asyncio.CancelledError:
                span.set_status(Status(StatusCode.ERROR, "client_disconnected"))
                raise
            except Exception as exc:
                log.exception("turn %s failed", turn_id)
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, "turn_failed"))
                emit("error", self.services.messages.error("turn_failed", retryable=True))
            finally:
                queue.put_nowait(_END)
