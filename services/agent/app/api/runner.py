"""Runs one turn through the main graph and yields typed SSE events.

The graph runs in its own task and feeds a queue, so the `turn` span and its
context live in one task for the whole turn. If the client disconnects, the
SSE generator stops and the task is cancelled.
"""

import asyncio
import itertools
import logging
import uuid
from collections.abc import AsyncIterator

from opentelemetry.context import Context
from opentelemetry.trace import Status, StatusCode

from app.api.events import DoneData, StateDigest, Usage, build_event
from app.api.schemas import TurnRequest
from app.errors import TurnError
from app.graph.deps import Services, TurnContext
from app.identity import hash_user_id
from app.observability.langfuse import set_trace_attributes
from app.observability.otel import current_trace_id, tracer

log = logging.getLogger(__name__)

_END = object()


class TurnRunner:
    def __init__(self, services: Services):
        self.services = services

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
                final: dict = {}
                async for mode, chunk in self.services.graph.astream(
                    graph_input,
                    config={"configurable": {"thread_id": req.conversation_id}},
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
