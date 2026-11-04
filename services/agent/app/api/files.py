"""My files, internal API (my-files PR, owner decisions 1 and 5; ADR-0442, ADR-0444). The caller is named by
X-User-Id and X-Tenant-Id, which the gateway sets from the verified token, never from client input (ADR-0059).
Every query names the owner: another user's file, like one that does not exist, is never listed and is 404
`attachment_not_found` on delete (G6). The gateway checks the owner first (its conversations table); this is
the second check.

- GET /v1/files: the caller's uploaded files of every conversation, newest first, searched by name or
  conversation title with `q`, filtered by type, status and upload date; knowledge base documents are never
  among them (decision 1).
- DELETE /v1/conversations/{conversation_id}/attachments/{attachment_id}: deletes the file from every index
and
  every conversation (ADR-0444); the gateway audits it. Deleting a file never deletes its conversation, and
  nothing deletes a conversation (decision 5)."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response

from app.api.ownership import Owner
from app.api.routes import _authorized, _caller, _error
from app.api.schemas import (
    ATTACHMENT_ID_PATTERN,
    CONVERSATION_ID_PATTERN,
    ErrorEnvelope,
    FileItem,
    FilePage,
)
from app.memory.types import MemoryScope
from app.rag.types import Owner as DataOwner

router = APIRouter(prefix="/v1", tags=["files"])

Caller = Annotated[MemoryScope, Depends(_caller)]
Kind = Literal["pdf", "docx", "xlsx", "txt", "md"]
_ERRORS = {400: {"model": ErrorEnvelope}, 401: {"model": ErrorEnvelope}}


@router.get("/files", response_model=FilePage, responses=_ERRORS)
async def list_files(
    request: Request,
    caller: Caller,
    q: str = Query(
        default="", max_length=200, description="Words of the file's name or its conversation's title."
    ),
    kind: list[Kind] = Query(default=[], description="Only files of these types."),
    status: Literal["pending", "indexed", "failed"] | None = Query(default=None),
    since: datetime | None = Query(default=None, description="Uploaded on or after this time."),
    until: datetime | None = Query(default=None, description="Uploaded before this time."),
    before: datetime | None = Query(default=None, description="`next_before` of the previous page."),
    limit: int = Query(default=30, ge=1, le=100),
    x_request_id: str | None = Header(default=None),
):
    """The caller's own files only (owner in every WHERE, ADR-0442); the gateway checks each returned
    conversation's owner again."""
    services = request.app.state.services
    request_id = x_request_id or str(uuid.uuid4())
    if not _authorized(request.headers.get("x-internal-token"), services.settings.agent_internal_token):
        return _error(request, request_id, "unauthorized", 401)
    if services.files is None:
        return FilePage(files=[])
    entries, next_before = await services.files.page(
        DataOwner(tenant_id=caller.tenant_id, user_id=caller.user_id),
        query=q,
        kinds=kind,
        status=status,
        since=since,
        until=until,
        before=before,
        limit=limit,
    )
    return FilePage(
        files=[
            FileItem(
                id=e.record.id,
                conversation_id=e.record.conversation_id,
                conversation_title=e.conversation_title or "",
                conversation_created_at=e.conversation_created_at,
                name=e.record.name,
                mime=e.record.mime,
                kind=e.record.kind,
                size=e.record.size,
                status=e.record.status,
                reason=e.record.reason,
                pages=e.record.pages,
                created_at=e.record.created_at,
            )
            for e in entries
        ],
        next_before=next_before,
    )


@router.delete(
    "/conversations/{conversation_id}/attachments/{attachment_id}",
    status_code=204,
    responses={
        **_ERRORS,
        403: {"model": ErrorEnvelope, "description": "conversation_forbidden: another owner's conversation"},
        404: {"model": ErrorEnvelope, "description": "attachment_not_found, also for another user's id"},
    },
)
async def delete_file(
    request: Request,
    caller: Caller,
    conversation_id: str = Path(pattern=CONVERSATION_ID_PATTERN),
    attachment_id: str = Path(pattern=ATTACHMENT_ID_PATTERN),
    x_request_id: str | None = Header(default=None),
):
    """Deletes one of the caller's files: its record, its chunks in the index and the recorded context packages
    naming it, and leaves its tombstone (ADR-0444, ADR-0448); the gateway then deletes its stored object
    (ADR-0450). Later turns and the history no longer show it."""
    services = request.app.state.services
    request_id = x_request_id or str(uuid.uuid4())
    if not _authorized(request.headers.get("x-internal-token"), services.settings.agent_internal_token):
        return _error(request, request_id, "unauthorized", 401)
    owner = await request.app.state.runner.owner_of(conversation_id)
    if owner is not None and owner != Owner(caller.user_id, caller.tenant_id):
        return _error(request, request_id, "conversation_forbidden", 403)
    deleted = (
        await services.files.delete(
            DataOwner(tenant_id=caller.tenant_id, user_id=caller.user_id), conversation_id, attachment_id
        )
        if services.files is not None
        else None
    )
    if deleted is None:
        return _error(request, request_id, "attachment_not_found", 404)
    return Response(status_code=204)
