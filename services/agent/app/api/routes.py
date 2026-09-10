"""Internal agent API (README §6.2). Reachable only from the gateway on the
compose network; requests carry the shared X-Internal-Token."""

import hmac
import uuid

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse
from opentelemetry import propagate

from app.api.events import to_sse
from app.api.schemas import ErrorEnvelope, HealthResponse, TurnRequest

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
