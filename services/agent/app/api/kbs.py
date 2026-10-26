"""Knowledge bases, internal API (phase 08, ADR-0333 to ADR-0339).

Admin endpoints (`/v1/admin/kbs`): only the gateway calls them, after checking the caller's `admin`
role, creating the KB's realm role and before auditing the call (ADR-0042); the agent checks the
role again from X-User-Admin (ADR-0284) and records the actor from X-User-Id with the names the
gateway read from the same verified token (ADR-0123). A KB is the caller's tenant's: another
tenant's slug, a deleted KB and an unknown slug are all 404 `kb_not_found` (I7).

`GET /v1/capabilities` (I3): the composer's capabilities and the KBs the caller may read, from the
caller's X-User-Permissions, which only the verified token gives."""

import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query, Request
from fastapi.responses import JSONResponse

from app.api.admin import _decoded
from app.api.routes import _authorized, _caller, _error
from app.api.schemas import (
    KB_DOCUMENT_ID_PATTERN,
    KB_SLUG_PATTERN,
    CapabilityList,
    CapabilityUi,
    CapabilityView,
    ErrorEnvelope,
    KbCreate,
    KbDeleted,
    KbDescription,
    KbDocumentList,
    KbDocumentRegistration,
    KbDocumentView,
    KbLabelText,
    KbList,
    KbSourceMetadata,
    KbToggle,
    KbView,
    LiveKbs,
)
from app.identity import Principal
from app.kb.service import TooManyUploads, UnsupportedType
from app.kb.store import Actor, InvalidSupersedes, KbDocumentRecord, KbExists, KbRecord, SourceMetadata
from app.memory.types import MemoryScope
from app.platform_settings import effective

router = APIRouter(tags=["knowledge-bases"])

# The permissions the gateway forwards: cap:*, kb:*:read and skill:* (ADR-0006, ADR-0297).
_PERMISSION = re.compile(r"^(cap:[a-z0-9_]+|kb:[a-z0-9_-]+:read|skill:[a-z][a-z0-9_-]{0,63})$")

_ADMIN_ERRORS = {
    400: {"model": ErrorEnvelope, "description": "invalid_request"},
    401: {"model": ErrorEnvelope},
    403: {"model": ErrorEnvelope, "description": "forbidden: the caller is not an admin (X-User-Admin)"},
}
_NOT_FOUND = {404: {"model": ErrorEnvelope, "description": "kb_not_found: none of the caller's tenant's live KBs"}}

Slug = Annotated[str, Path(pattern=KB_SLUG_PATTERN)]
DocumentId = Annotated[str, Path(pattern=KB_DOCUMENT_ID_PATTERN)]


class KbCall:
    def __init__(self, request: Request, actor: Actor, tenant_id: str, request_id: str):
        self.request, self.actor, self.tenant_id, self.request_id = request, actor, tenant_id, request_id
        self.services = request.app.state.services

    def error(self, code: str, status: int, details: list[str] | None = None) -> JSONResponse:
        envelope = ErrorEnvelope.of(self.request_id, self.services.messages.error(code), details)
        return JSONResponse(envelope.model_dump(exclude_none=True), status_code=status)


async def _admin(
    request: Request,
    x_user_id: Annotated[str, Header(min_length=1, max_length=256, description="The admin making the call.")],
    x_tenant_id: Annotated[str, Header(min_length=1, max_length=256, description="The admin's tenant.")],
    x_user_admin: Annotated[
        str | None, Header(description="`true` when the gateway verified the caller's admin role (ADR-0284).")
    ] = None,
    x_internal_token: Annotated[str | None, Header()] = None,
    x_request_id: Annotated[str | None, Header()] = None,
    x_user_name: Annotated[
        str | None, Header(max_length=1024, description="The admin's username, percent-encoded UTF-8.")
    ] = None,
    x_user_display_name: Annotated[
        str | None, Header(max_length=1024, description="The admin's display name, percent-encoded UTF-8.")
    ] = None,
):
    actor = Actor(user_id=x_user_id, username=_decoded(x_user_name), name=_decoded(x_user_display_name))
    call = KbCall(request, actor, x_tenant_id, x_request_id or str(uuid.uuid4()))
    if not _authorized(x_internal_token, call.services.settings.agent_internal_token):
        return call.error("unauthorized", 401)
    if (x_user_admin or "").strip().lower() != "true":
        return call.error("forbidden", 403)
    return call


Admin = Annotated[KbCall | JSONResponse, Depends(_admin)]


def _kb_view(kb: KbRecord, counts: dict[str, tuple[int, int]]) -> KbView:
    total, indexed = counts.get(kb.slug, (0, 0))
    return KbView(
        slug=kb.slug,
        name=KbLabelText(ar=kb.name_ar, en=kb.name_en),
        description=KbDescription(ar=kb.description_ar, en=kb.description_en),
        icon=kb.icon,
        role=kb.role,
        documents=total,
        indexed=indexed,
        created_at=kb.created_at,
        created_by_username=kb.created_by_username,
        created_by_name=kb.created_by_name,
    )


def _document_view(record: KbDocumentRecord, superseded_by: str | None = None) -> KbDocumentView:
    return KbDocumentView(
        id=record.id,
        kb=record.kb_slug,
        name=record.name,
        kind=record.kind,
        size=record.size,
        status=record.status,
        reason=record.reason,
        pages=record.pages,
        chunks=record.chunks,
        warnings=record.warnings,
        metadata=KbSourceMetadata(
            title=record.title,
            publisher=record.publisher,
            source_url=record.source_url,
            published_on=record.published_on,
            supersedes=record.supersedes,
        ),
        superseded_by=superseded_by,
        created_at=record.created_at,
        indexed_at=record.indexed_at,
        uploaded_by_username=record.uploaded_by_username,
        uploaded_by_name=record.uploaded_by_name,
    )


def _metadata(body: KbSourceMetadata) -> SourceMetadata:
    return SourceMetadata(
        title=body.title.strip(),
        publisher=body.publisher.strip(),
        source_url=body.source_url,
        published_on=body.published_on,
        supersedes=body.supersedes,
    )


# ------------------------------------------------------------------ admin: knowledge bases


@router.get("/v1/admin/kbs", response_model=KbList, responses=_ADMIN_ERRORS)
async def list_kbs(call: Admin):
    if isinstance(call, JSONResponse):
        return call
    store = call.services.kbs.store
    counts = await store.counts(call.tenant_id)
    return KbList(kbs=[_kb_view(kb, counts) for kb in await store.list_active(call.tenant_id)])


@router.post(
    "/v1/admin/kbs",
    status_code=201,
    response_model=KbView,
    responses={**_ADMIN_ERRORS, 409: {"model": ErrorEnvelope, "description": "kb_exists: the slug is taken"}},
)
async def create_kb(body: KbCreate, call: Admin):
    if isinstance(call, JSONResponse):
        return call
    try:
        kb = await call.services.kbs.store.create(
            call.tenant_id,
            body.slug,
            name_ar=body.name.ar.strip(),
            name_en=body.name.en.strip(),
            description_ar=body.description.ar.strip(),
            description_en=body.description.en.strip(),
            icon=body.icon,
            actor=call.actor,
        )
    except KbExists:
        return call.error("kb_exists", 409)
    return JSONResponse(_kb_view(kb, {}).model_dump(mode="json"), status_code=201)


@router.get("/v1/admin/kbs/{slug}", response_model=KbView, responses={**_ADMIN_ERRORS, **_NOT_FOUND})
async def get_kb(slug: Slug, call: Admin):
    if isinstance(call, JSONResponse):
        return call
    store = call.services.kbs.store
    kb = await store.get(call.tenant_id, slug)
    if kb is None:
        return call.error("kb_not_found", 404)
    return _kb_view(kb, await store.counts(call.tenant_id))


@router.delete("/v1/admin/kbs/{slug}", response_model=KbDeleted, responses={**_ADMIN_ERRORS, **_NOT_FOUND})
async def delete_kb(slug: Slug, call: Admin):
    """Soft delete (I1): hidden, role kept, its documents purged from the index."""
    if isinstance(call, JSONResponse):
        return call
    deleted = await call.services.kbs.delete(call.tenant_id, slug, call.actor)
    if deleted is None:
        return call.error("kb_not_found", 404)
    kb, purged = deleted
    return KbDeleted(slug=kb.slug, role=kb.role, index_purged=purged)


# ------------------------------------------------------------------ admin: documents


@router.get(
    "/v1/admin/kbs/{slug}/documents", response_model=KbDocumentList, responses={**_ADMIN_ERRORS, **_NOT_FOUND}
)
async def list_documents(slug: Slug, call: Admin):
    if isinstance(call, JSONResponse):
        return call
    if await call.services.kbs.store.get(call.tenant_id, slug) is None:
        return call.error("kb_not_found", 404)
    records = await call.services.kbs.documents(call.tenant_id, slug)
    superseded_by = {r.supersedes: r.id for r in records if r.supersedes}
    return KbDocumentList(documents=[_document_view(r, superseded_by.get(r.id)) for r in records])


@router.post(
    "/v1/admin/kbs/{slug}/documents",
    status_code=201,
    response_model=KbDocumentView,
    responses={
        **_ADMIN_ERRORS,
        **_NOT_FOUND,
        409: {"model": ErrorEnvelope, "description": "kb_supersedes_invalid: the earlier version named is not valid"},
        415: {"model": ErrorEnvelope, "description": "file_type_unsupported"},
        429: {"model": ErrorEnvelope, "description": "too_many_uploads"},
    },
)
async def register_document(slug: Slug, body: KbDocumentRegistration, call: Admin):
    """Registers a stored KB document as `pending` with its D3 metadata and hands it to the KB backend
    (I2), answering within register_response_s."""
    if isinstance(call, JSONResponse):
        return call
    service = call.services.kbs
    kb = await service.store.get(call.tenant_id, slug)
    if kb is None:
        return call.error("kb_not_found", 404)
    try:
        with effective.pin(call.services.platform.current if call.services.platform is not None else None):
            record = await service.register(
                kb,
                name=body.name,
                mime=body.mime,
                size=body.size,
                object_key=body.object_key,
                metadata=_metadata(body.metadata),
                actor=call.actor,
            )
    except UnsupportedType:
        return call.error("file_type_unsupported", 415)
    except TooManyUploads:
        return call.error("too_many_uploads", 429)
    except InvalidSupersedes as exc:
        return call.error("kb_supersedes_invalid", 409, [str(exc)])
    return JSONResponse(_document_view(record).model_dump(mode="json"), status_code=201)


@router.get(
    "/v1/admin/kbs/{slug}/documents/{document_id}",
    response_model=KbDocumentView,
    responses={**_ADMIN_ERRORS, 404: {"model": ErrorEnvelope, "description": "kb_not_found or kb_document_not_found"}},
)
async def get_document(slug: Slug, document_id: DocumentId, call: Admin):
    if isinstance(call, JSONResponse):
        return call
    if await call.services.kbs.store.get(call.tenant_id, slug) is None:
        return call.error("kb_not_found", 404)
    record = await call.services.kbs.document(call.tenant_id, slug, document_id)
    if record is None:
        return call.error("kb_document_not_found", 404)
    return _document_view(record)


@router.put(
    "/v1/admin/kbs/{slug}/documents/{document_id}/metadata",
    response_model=KbDocumentView,
    responses={
        **_ADMIN_ERRORS,
        404: {"model": ErrorEnvelope, "description": "kb_not_found or kb_document_not_found"},
        409: {"model": ErrorEnvelope, "description": "kb_supersedes_invalid"},
    },
)
async def update_metadata(slug: Slug, document_id: DocumentId, body: KbSourceMetadata, call: Admin):
    """Corrects a document's D3 metadata, its supersedes link included."""
    if isinstance(call, JSONResponse):
        return call
    store = call.services.kbs.store
    if await store.get(call.tenant_id, slug) is None:
        return call.error("kb_not_found", 404)
    try:
        record = await store.update_metadata(call.tenant_id, slug, document_id, _metadata(body))
    except InvalidSupersedes as exc:
        return call.error("kb_supersedes_invalid", 409, [str(exc)])
    if record is None:
        return call.error("kb_document_not_found", 404)
    return _document_view(record)


# ------------------------------------------------------------------ the composer (I3)


@router.get(
    "/v1/capabilities",
    response_model=CapabilityList,
    responses={400: {"model": ErrorEnvelope}, 401: {"model": ErrorEnvelope}},
)
async def list_capabilities(
    request: Request,
    caller: Annotated[MemoryScope, Depends(_caller)],
    x_user_permissions: Annotated[
        str,
        Header(max_length=8192, description="The caller's permissions, comma-separated, from the verified token."),
    ] = "",
    x_internal_token: str | None = Header(default=None),
    x_request_id: str | None = Header(default=None),
):
    """The caller's capabilities (README §18.4: manifests filtered by the principal) and the
    knowledge bases it may read, its tenant's live ones it holds kb:<slug>:read for (I3, I7)."""
    services = request.app.state.services
    request_id = x_request_id or str(uuid.uuid4())
    if not _authorized(x_internal_token, services.settings.agent_internal_token):
        return _error(request, request_id, "unauthorized", 401)
    permissions = tuple(p for p in (part.strip() for part in x_user_permissions.split(",")) if p)
    if any(not _PERMISSION.match(p) for p in permissions):
        return _error(request, request_id, "invalid_request", 400)
    principal = Principal(user_id=caller.user_id, tenant_id=caller.tenant_id, permissions=permissions)
    capabilities = sorted(services.registry.available_for(principal), key=lambda c: c.manifest.ui.order)
    kbs = await services.kbs.readable(principal) if services.kbs is not None else []
    return CapabilityList(
        capabilities=[
            CapabilityView(
                slug=cap.slug,
                name=cap.manifest.name.model_dump(),
                description=cap.manifest.description.model_dump(),
                icon=cap.manifest.icon,
                ui=CapabilityUi(mode=cap.manifest.ui.mode, order=cap.manifest.ui.order),
            )
            for cap in capabilities
        ],
        knowledge_bases=[
            KbToggle(
                slug=kb.slug,
                name=KbLabelText(ar=kb.name_ar, en=kb.name_en),
                description=KbDescription(ar=kb.description_ar, en=kb.description_en),
                icon=kb.icon,
            )
            for kb in kbs
        ],
    )


# ------------------------------------------------------------------ the gateway's own check (I3, I7)


@router.get(
    "/v1/internal/kbs/live",
    response_model=LiveKbs,
    responses={400: {"model": ErrorEnvelope}, 401: {"model": ErrorEnvelope}},
)
async def live_kbs(
    request: Request,
    x_tenant_id: Annotated[str, Header(min_length=1, max_length=256, description="The caller's tenant.")],
    slug: Annotated[list[str], Query(max_length=32, description="The slugs a turn selected.")] = [],  # noqa: B006
    x_internal_token: str | None = Header(default=None),
    x_request_id: str | None = Header(default=None),
):
    """Which of a turn's selected slugs are live KBs of the caller's tenant: the gateway refuses the
    others itself, before the agent's own check (phase 08, I7)."""
    services = request.app.state.services
    request_id = x_request_id or str(uuid.uuid4())
    if not _authorized(x_internal_token, services.settings.agent_internal_token):
        return _error(request, request_id, "unauthorized", 401)
    if any(not re.match(KB_SLUG_PATTERN, s) for s in slug):
        return _error(request, request_id, "invalid_request", 400)
    live = await services.kbs.store.active_slugs(x_tenant_id, slug) if services.kbs is not None else set()
    return LiveKbs(live=[s for s in dict.fromkeys(slug) if s in live])
