"""A user's conversations and a conversation's history, internal API (ADR-0192 to ADR-0194).
The caller is named by X-User-Id and X-Tenant-Id, which the gateway sets from the verified
token (ADR-0059); there is no other way to name a user. Another owner's conversation is 403
`conversation_forbidden`; a conversation nobody has used holds no messages. Read-only: the
product deletes nothing (ADR-0166)."""

import logging
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query, Request

from app.api.history import first_question, history
from app.api.ownership import Owner, title_of
from app.api.routes import _authorized, _caller, _error
from app.api.schemas import (
    CONVERSATION_ID_PATTERN,
    ConversationList,
    ConversationMessages,
    ConversationSummary,
    ErrorEnvelope,
    HistoryAttachment,
    HistoryMessage,
)
from app.memory.types import MemoryScope
from app.rag.types import Owner as DataOwner

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])

Caller = Annotated[MemoryScope, Depends(_caller)]
ConversationId = Annotated[str, Path(pattern=CONVERSATION_ID_PATTERN)]

# Rows recorded before titles existed, read from their threads per list request (ADR-0192).
BACKFILL_PER_REQUEST = 50

_ERRORS = {400: {"model": ErrorEnvelope}, 401: {"model": ErrorEnvelope}}


def _thread(conversation_id: str) -> dict:
    return {"configurable": {"thread_id": conversation_id}}


async def _backfill(request: Request, owner: Owner) -> None:
    """Titles and turn times for the owner's rows from before ADR-0192, from their threads."""
    services = request.app.state.services
    for conversation_id in await services.owners.untitled(owner, BACKFILL_PER_REQUEST):
        try:
            snapshot = await services.graph.aget_state(_thread(conversation_id))
            question = first_question((snapshot.values or {}).get("messages") or [])
            at = datetime.fromisoformat(snapshot.created_at) if question and snapshot.created_at else None
            await services.owners.fill(conversation_id, title_of(question) if at else "", at)
        except Exception as exc:  # one unreadable thread never fails the list
            log.warning("conversation %s not backfilled: %s", conversation_id, type(exc).__name__)


@router.get("", response_model=ConversationList, responses=_ERRORS)
async def list_conversations(
    request: Request,
    caller: Caller,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    before: Annotated[datetime | None, Query(description="`next_before` of the previous page.")] = None,
    x_internal_token: str | None = Header(default=None),
    x_request_id: str | None = Header(default=None),
):
    """The caller's conversations with a finished turn, newest first (ADR-0192)."""
    services = request.app.state.services
    request_id = x_request_id or str(uuid.uuid4())
    if not _authorized(x_internal_token, services.settings.agent_internal_token):
        return _error(request, request_id, "unauthorized", 401)
    owner = Owner(caller.user_id, caller.tenant_id)
    await _backfill(request, owner)
    rows = await services.owners.listed(owner, limit + 1, before)
    page = rows[:limit]
    return ConversationList(
        conversations=[
            ConversationSummary(id=row.conversation_id, title=row.title, last_turn_at=row.last_turn_at)
            for row in page
        ],
        next_before=page[-1].last_turn_at if len(rows) > limit else None,
    )


@router.get(
    "/{conversation_id}/messages",
    response_model=ConversationMessages,
    responses={
        **_ERRORS,
        403: {"model": ErrorEnvelope, "description": "conversation_forbidden: another owner's conversation"},
    },
)
async def conversation_messages(
    request: Request,
    conversation_id: ConversationId,
    caller: Caller,
    x_internal_token: str | None = Header(default=None),
    x_request_id: str | None = Header(default=None),
):
    """The conversation's messages, oldest first, as the model may see them again (ADR-0193)."""
    services = request.app.state.services
    request_id = x_request_id or str(uuid.uuid4())
    if not _authorized(x_internal_token, services.settings.agent_internal_token):
        return _error(request, request_id, "unauthorized", 401)
    owner = await request.app.state.runner.owner_of(conversation_id)
    if owner is None:
        return ConversationMessages(conversation_id=conversation_id, messages=[])
    if owner != Owner(caller.user_id, caller.tenant_id):
        return _error(request, request_id, "conversation_forbidden", 403)
    snapshot = await services.graph.aget_state(_thread(conversation_id))
    shown = history((snapshot.values or {}).get("messages") or [])
    names: dict[str, str] = {}
    if any(m.get("attachment_ids") for m in shown):
        records = await services.attachments.store.list_for(
            DataOwner(tenant_id=caller.tenant_id, user_id=caller.user_id), conversation_id
        )
        names = {r.id: r.name for r in records}
    messages = [
        HistoryMessage(
            role=m["role"],
            text=m["text"],
            turn_id=m.get("turn_id"),
            # Only this owner's files of this conversation are named; any other id is dropped.
            attachments=[
                HistoryAttachment(id=i, name=names[i]) for i in m.get("attachment_ids", []) if i in names
            ],
            citations=m.get("citations", []),
        )
        for m in shown
    ]
    return ConversationMessages(conversation_id=conversation_id, messages=messages)
