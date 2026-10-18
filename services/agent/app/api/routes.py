"""Internal agent API (README §6.2). Reachable only from the gateway on the
compose network; requests carry the shared X-Internal-Token. Endpoints without a
request body name their caller in X-User-Id and X-Tenant-Id, which the gateway
sets from the verified token, never from client input (ADR-0059)."""

import dataclasses
import hmac
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from opentelemetry import propagate
from starlette.background import BackgroundTask

from app.api.events import to_sse
from app.api.locks import LockPoolBusy
from app.api.ownership import Owner
from app.api.schemas import (
    CONVERSATION_ID_PATTERN,
    MEMORY_ITEM_ID_PATTERN,
    TURN_ID_PATTERN,
    DebugContext,
    ErrorEnvelope,
    HealthResponse,
    MemoryItemView,
    MemoryList,
    MemorySettings,
    RecordedContext,
    RetractRequest,
    TurnRequest,
)
from app.memory.types import MemoryScope

router = APIRouter(prefix="/v1")

_SSE_DESCRIPTION = "Server-sent events; each `data` is an AgentEvent (docs/events.schema.json)."


def _authorized(token: str | None, expected: str) -> bool:
    return bool(expected) and token is not None and hmac.compare_digest(token.encode(), expected.encode())


def _error(request: Request, request_id: str, code: str, status: int) -> JSONResponse:
    envelope = ErrorEnvelope.of(request_id, request.app.state.services.messages.error(code))
    return JSONResponse(envelope.model_dump(), status_code=status)


def _caller(
    x_user_id: Annotated[str, Header(min_length=1, max_length=256, description="The caller's user id.")],
    x_tenant_id: Annotated[str, Header(min_length=1, max_length=256, description="The caller's tenant id.")],
) -> MemoryScope:
    return MemoryScope(tenant_id=x_tenant_id, user_id=x_user_id)


Caller = Annotated[MemoryScope, Depends(_caller)]
InternalToken = Annotated[str | None, Header()]
RequestId = Annotated[str | None, Header()]

_CALLER_ERRORS = {400: {"model": ErrorEnvelope}, 401: {"model": ErrorEnvelope}}


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    services = request.app.state.services
    # A server that is down never makes the agent unhealthy (I8): it is reported, not a failure.
    mcp = services.broker.mcp_health()
    return HealthResponse(status="ok", capabilities=[c.slug for c in services.registry.enabled()], mcp=mcp)


@router.post(
    "/turns",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": _SSE_DESCRIPTION,
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        },
        400: {"model": ErrorEnvelope},
        401: {"model": ErrorEnvelope},
        403: {"model": ErrorEnvelope, "description": "conversation_forbidden: another owner's conversation"},
        409: {"model": ErrorEnvelope, "description": "turn_in_flight: another turn holds the conversation"},
        503: {"model": ErrorEnvelope, "description": "service_busy: every turn lock connection is in use"},
    },
)
async def create_turn(
    body: TurnRequest,
    request: Request,
    x_internal_token: str | None = Header(default=None),
    x_request_id: str | None = Header(default=None),
):
    services = request.app.state.services
    request_id = x_request_id or str(uuid.uuid4())
    if not _authorized(x_internal_token, services.settings.agent_internal_token):
        return _error(request, request_id, "unauthorized", 401)

    parent = propagate.extract(request.headers)
    runner = request.app.state.runner
    # Ownership first: a non-owner never takes the conversation's lock (ADR-0050, ADR-0109).
    if not await runner.claim(body.conversation_id, body.principal):
        return _error(request, request_id, "conversation_forbidden", 403)
    # One turn at a time per conversation (D6): refused before any stream starts.
    try:
        lease = await runner.lock(body.conversation_id)
    except LockPoolBusy:
        return _error(request, request_id, "service_busy", 503)
    if lease is None:
        return _error(request, request_id, "turn_in_flight", 409)

    async def events():
        async for event in runner.stream(body, request_id, parent, lease=lease):
            yield to_sse(event)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Request-Id": request_id},
        # Idempotent; covers a client gone before the stream started.
        background=BackgroundTask(lease.release),
    )


@router.post(
    "/conversations/{conversation_id}/turns/{turn_id}/retract",
    status_code=204,
    response_class=Response,
    responses={
        204: {"description": "The reply is marked retracted; Context Assembly no longer includes it."},
        400: {"model": ErrorEnvelope},
        401: {"model": ErrorEnvelope},
        403: {"model": ErrorEnvelope, "description": "conversation_forbidden"},
        404: {"model": ErrorEnvelope, "description": "turn_not_found"},
        409: {"model": ErrorEnvelope, "description": "turn_in_flight: a turn kept the conversation locked"},
        503: {"model": ErrorEnvelope, "description": "service_busy: every turn lock connection is in use"},
    },
)
async def retract_turn(
    body: RetractRequest,
    request: Request,
    conversation_id: str = Path(pattern=CONVERSATION_ID_PATTERN),
    turn_id: str = Path(pattern=TURN_ID_PATTERN),
    x_internal_token: str | None = Header(default=None),
    x_request_id: str | None = Header(default=None),
):
    """Withdraws a persisted reply from the history (ADR-0048). Called by the gateway
    when a finished turn's audit record cannot be written."""
    services = request.app.state.services
    request_id = x_request_id or str(uuid.uuid4())
    if not _authorized(x_internal_token, services.settings.agent_internal_token):
        return _error(request, request_id, "unauthorized", 401)

    outcome = await request.app.state.runner.retract(conversation_id, turn_id, body.principal, body.reason)
    if outcome == "retracted":
        return Response(status_code=204)
    code, status = {
        "forbidden": ("conversation_forbidden", 403),
        "in_flight": ("turn_in_flight", 409),
        "busy": ("service_busy", 503),
    }.get(outcome, ("turn_not_found", 404))
    return _error(request, request_id, code, status)


@router.get("/memory", response_model=MemoryList, responses=_CALLER_ERRORS)
async def list_memory(
    request: Request, caller: Caller, x_internal_token: InternalToken = None, x_request_id: RequestId = None
):
    """The caller's own memory items and memory switch (README §8). No endpoint reads
    another user's memory."""
    services = request.app.state.services
    request_id = x_request_id or str(uuid.uuid4())
    if not _authorized(x_internal_token, services.settings.agent_internal_token):
        return _error(request, request_id, "unauthorized", 401)
    store = services.memory.store
    items = [MemoryItemView(**dataclasses.asdict(item)) for item in await store.list_items(caller)]
    return MemoryList(enabled=await store.enabled(caller), items=items)


@router.delete(
    "/memory/{item_id}",
    status_code=204,
    response_class=Response,
    responses={
        204: {"description": "The item is deleted."},
        **_CALLER_ERRORS,
        404: {"model": ErrorEnvelope, "description": "memory_not_found, also for another user's item"},
    },
)
async def delete_memory(
    request: Request,
    caller: Caller,
    item_id: str = Path(pattern=MEMORY_ITEM_ID_PATTERN),
    x_internal_token: InternalToken = None,
    x_request_id: RequestId = None,
):
    services = request.app.state.services
    request_id = x_request_id or str(uuid.uuid4())
    if not _authorized(x_internal_token, services.settings.agent_internal_token):
        return _error(request, request_id, "unauthorized", 401)
    if not await services.memory.store.delete(caller, item_id):
        return _error(request, request_id, "memory_not_found", 404)
    return Response(status_code=204)


@router.put("/memory/settings", response_model=MemorySettings, responses=_CALLER_ERRORS)
async def update_memory_settings(
    body: MemorySettings,
    request: Request,
    caller: Caller,
    x_internal_token: InternalToken = None,
    x_request_id: RequestId = None,
):
    """Turns the caller's memory on or off. Off, nothing is written or retrieved for
    them; stored items stay until they delete them."""
    services = request.app.state.services
    request_id = x_request_id or str(uuid.uuid4())
    if not _authorized(x_internal_token, services.settings.agent_internal_token):
        return _error(request, request_id, "unauthorized", 401)
    return MemorySettings(enabled=await services.memory.store.set_enabled(caller, body.enabled))


@router.get(
    "/debug/context/{turn_id}",
    response_model=DebugContext,
    responses={
        **_CALLER_ERRORS,
        403: {"model": ErrorEnvelope, "description": "conversation_forbidden"},
        404: {"model": ErrorEnvelope, "description": "not_found (disabled) or turn_not_found"},
    },
)
async def debug_context(
    request: Request,
    caller: Caller,
    turn_id: str = Path(pattern=TURN_ID_PATTERN),
    x_internal_token: InternalToken = None,
    x_request_id: RequestId = None,
):
    """What every model call of a turn saw (README §7). Off unless DEBUG_CONTEXT_ENABLED.
    It reaches a conversation outside a turn, so it does not go through the turn
    runner's binding and checks ownership itself: only the conversation's owner
    reads it (ADR-0059)."""
    services = request.app.state.services
    request_id = x_request_id or str(uuid.uuid4())
    if not _authorized(x_internal_token, services.settings.agent_internal_token):
        return _error(request, request_id, "unauthorized", 401)
    if not services.settings.debug_context_enabled:
        return _error(request, request_id, "not_found", 404)
    recorded = await services.context_log.turn(turn_id)
    if recorded is None:
        return _error(request, request_id, "turn_not_found", 404)
    owner = await request.app.state.runner.owner_of(recorded.conversation_id)
    if owner != Owner(caller.user_id, caller.tenant_id):
        return _error(request, request_id, "conversation_forbidden", 403)
    return DebugContext(
        turn_id=turn_id,
        conversation_id=recorded.conversation_id,
        packages=[
            RecordedContext(node=p.node, created_at=p.created_at, package=p.package)
            for p in recorded.packages
        ],
    )
