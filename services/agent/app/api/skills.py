"""The skills a caller may use, internal API (phase 06, ADR-0301): for the composer's picker.
The caller is named by X-User-Id and X-Tenant-Id and its permissions by X-User-Permissions, which
the gateway sets from the verified token, never from client input (ADR-0059 precedent). The list
is read from the in-memory registries only (G7): no database, Redis or model call."""

import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request

from app.api.routes import _authorized, _caller, _error
from app.api.schemas import ErrorEnvelope, SkillList, SkillView
from app.identity import Principal
from app.memory.types import MemoryScope
from app.skills.registry import available_skills

router = APIRouter(prefix="/v1/skills", tags=["skills"])

# The permissions the gateway forwards: cap:*, kb:*:read and skill:* (ADR-0006, ADR-0297).
_PERMISSION = re.compile(r"^(cap:[a-z0-9_]+|kb:[a-z0-9_-]+:read|skill:[a-z0-9_]+)$")


@router.get("", response_model=SkillList, responses={400: {"model": ErrorEnvelope}, 401: {"model": ErrorEnvelope}})
async def list_skills(
    request: Request,
    caller: Annotated[MemoryScope, Depends(_caller)],
    x_user_permissions: Annotated[
        str,
        Header(max_length=8192, description="The caller's permissions, comma-separated, from the verified token."),
    ] = "",
    x_internal_token: str | None = Header(default=None),
    x_request_id: str | None = Header(default=None),
):
    services = request.app.state.services
    request_id = x_request_id or str(uuid.uuid4())
    if not _authorized(x_internal_token, services.settings.agent_internal_token):
        return _error(request, request_id, "unauthorized", 401)
    permissions = tuple(p for p in (part.strip() for part in x_user_permissions.split(",")) if p)
    if any(not _PERMISSION.match(p) for p in permissions):
        return _error(request, request_id, "invalid_request", 400)
    principal = Principal(user_id=caller.user_id, tenant_id=caller.tenant_id, permissions=permissions)
    return SkillList(
        skills=[
            SkillView(
                slug=s.slug,
                name=s.manifest.name.model_dump(),
                description=s.manifest.description.model_dump(),
            )
            for s in available_skills(services, principal)
        ]
    )
