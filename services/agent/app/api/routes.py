"""Internal agent API (README §6.2). Reachable only from the gateway on the
compose network; requests carry the shared X-Internal-Token."""

import hmac
import uuid

from fastapi import APIRouter, Header, Path, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from opentelemetry import propagate

from app.api.events import to_sse
from app.api.schemas import (
    CONVERSATION_ID_PATTERN,
    TURN_ID_PATTERN,
    ErrorEnvelope,
    HealthResponse,
    RetractRequest,
    TurnRequest,
)

router = APIRouter(prefix="/v1")

_SSE_DESCRIPTION = "Server-sent events; each `data` is an AgentEvent (docs/events.schema.json)."


def _authorized(token: str | None, expected: str) -> bool:
    return bool(expected) and token is not None and hmac.compare_digest(token.encode(), expected.encode())


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    services = request.app.state.services
    return HealthResponse(status="ok", capabilities=[c.slug for c in services.registry.enabled()])


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
        envelope = ErrorEnvelope.of(request_id, services.messages.error("unauthorized"))
        return JSONResponse(envelope.model_dump(), status_code=401)

    parent = propagate.extract(request.headers)
    runner = request.app.state.runner

    async def events():
        async for event in runner.stream(body, request_id, parent):
            yield to_sse(event)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Request-Id": request_id},
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
        envelope = ErrorEnvelope.of(request_id, services.messages.error("unauthorized"))
        return JSONResponse(envelope.model_dump(), status_code=401)

    outcome = await request.app.state.runner.retract(conversation_id, turn_id, body.principal, body.reason)
    if outcome == "retracted":
        return Response(status_code=204)
    code, status = ("conversation_forbidden", 403) if outcome == "forbidden" else ("turn_not_found", 404)
    envelope = ErrorEnvelope.of(request_id, services.messages.error(code))
    return JSONResponse(envelope.model_dump(), status_code=status)
