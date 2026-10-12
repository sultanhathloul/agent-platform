"""A conversation's attachments, internal API (phase 04, A2; ADR-0129). The gateway stores
an upload in MinIO and registers it here; list, status and the object key for a download are
answered for the conversation's owner only. There is no delete: the product deletes no file
(owner decision S4). A caller is named by the
principal in the body (registration) or by X-User-Id and X-Tenant-Id, which the gateway
sets from the verified token (ADR-0059). Another owner's conversation is 403
`conversation_forbidden`; an attachment id the caller does not have in this conversation,
another user's included, is 404 `attachment_not_found` (G6)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Request
from fastapi.responses import JSONResponse

from app.api.ownership import Owner
from app.api.routes import _authorized, _caller, _error
from app.api.schemas import (
    ATTACHMENT_ID_PATTERN,
    CONVERSATION_ID_PATTERN,
    AttachmentList,
    AttachmentRegistration,
    AttachmentView,
    ErrorEnvelope,
)
from app.attachments.service import UnsupportedType
from app.attachments.store import AttachmentRecord, LimitReached
from app.memory.types import MemoryScope
from app.rag.types import Owner as DataOwner

router = APIRouter(prefix="/v1/conversations/{conversation_id}/attachments", tags=["attachments"])

Caller = Annotated[MemoryScope, Depends(_caller)]
ConversationId = Annotated[str, Path(pattern=CONVERSATION_ID_PATTERN)]
AttachmentId = Annotated[str, Path(pattern=ATTACHMENT_ID_PATTERN)]

_ERRORS = {
    400: {"model": ErrorEnvelope},
    401: {"model": ErrorEnvelope},
    403: {"model": ErrorEnvelope, "description": "conversation_forbidden: another owner's conversation"},
}
_NOT_FOUND = {
    404: {"model": ErrorEnvelope, "description": "attachment_not_found, also for another user's id"}
}


def _view(record: AttachmentRecord) -> AttachmentView:
    return AttachmentView.model_validate(record.model_dump())


async def _owned(request: Request, conversation_id: str, caller: MemoryScope) -> bool:
    """False when the conversation belongs to someone else; a conversation nobody has used
    yet is the caller's to read (it holds nothing)."""
    owner = await request.app.state.runner.owner_of(conversation_id)
    return owner is None or owner == Owner(caller.user_id, caller.tenant_id)


def _request_id(header: str | None) -> str:
    return header or str(uuid.uuid4())


@router.post(
    "",
    status_code=201,
    response_model=AttachmentView,
    responses={
        **_ERRORS,
        409: {"model": ErrorEnvelope, "description": "attachment_limit: the conversation holds the maximum"},
        415: {"model": ErrorEnvelope, "description": "file_type_unsupported"},
    },
)
async def register_attachment(
    body: AttachmentRegistration,
    request: Request,
    conversation_id: ConversationId,
    x_internal_token: str | None = Header(default=None),
    x_request_id: str | None = Header(default=None),
):
    """Registers an upload as `pending` and queues its ingestion (ADR-0130). The first use
    of a conversation binds it to the caller, as a first turn does (ADR-0050)."""
    services = request.app.state.services
    request_id = _request_id(x_request_id)
    if not _authorized(x_internal_token, services.settings.agent_internal_token):
        return _error(request, request_id, "unauthorized", 401)
    if not await request.app.state.runner.claim(conversation_id, body.principal):
        return _error(request, request_id, "conversation_forbidden", 403)
    owner = DataOwner(tenant_id=body.principal.tenant_id, user_id=body.principal.user_id)
    try:
        record = await services.attachments.register(
            owner, conversation_id, name=body.name, mime=body.mime, size=body.size, object_key=body.object_key
        )
    except UnsupportedType:
        return _error(request, request_id, "file_type_unsupported", 415)
    except LimitReached:
        return _error(request, request_id, "attachment_limit", 409)
    return JSONResponse(_view(record).model_dump(mode="json"), status_code=201)


@router.get("", response_model=AttachmentList, responses=_ERRORS)
async def list_attachments(
    request: Request,
    conversation_id: ConversationId,
    caller: Caller,
    x_internal_token: str | None = Header(default=None),
    x_request_id: str | None = Header(default=None),
):
    services = request.app.state.services
    request_id = _request_id(x_request_id)
    if not _authorized(x_internal_token, services.settings.agent_internal_token):
        return _error(request, request_id, "unauthorized", 401)
    if not await _owned(request, conversation_id, caller):
        return _error(request, request_id, "conversation_forbidden", 403)
    owner = DataOwner(tenant_id=caller.tenant_id, user_id=caller.user_id)
    records = await services.attachments.with_status(owner, conversation_id)
    return AttachmentList(attachments=[_view(r) for r in records])


@router.get("/{attachment_id}", response_model=AttachmentView, responses={**_ERRORS, **_NOT_FOUND})
async def get_attachment(
    request: Request,
    conversation_id: ConversationId,
    attachment_id: AttachmentId,
    caller: Caller,
    x_internal_token: str | None = Header(default=None),
    x_request_id: str | None = Header(default=None),
):
    """The attachment's status (pending, indexed, failed with a reason) and, for the
    gateway's download, its object key."""
    services = request.app.state.services
    request_id = _request_id(x_request_id)
    if not _authorized(x_internal_token, services.settings.agent_internal_token):
        return _error(request, request_id, "unauthorized", 401)
    if not await _owned(request, conversation_id, caller):
        return _error(request, request_id, "conversation_forbidden", 403)
    owner = DataOwner(tenant_id=caller.tenant_id, user_id=caller.user_id)
    record = await services.attachments.get_with_status(owner, conversation_id, attachment_id)
    if record is None:
        return _error(request, request_id, "attachment_not_found", 404)
    return _view(record)
