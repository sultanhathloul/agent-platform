"""Reports, internal API (reports PR, I5, I6, ADR-0373, ADR-0374). The caller is named by X-User-Id and
X-Tenant-Id, which the gateway sets from the verified token, never from client input (ADR-0059); every
read is the owner's only: a report of another user or tenant, like one that does not exist, is 404
`report_not_found`, so an id reveals nothing. The gateway checks the owner first; this is the second check.

- GET /v1/reports: the caller's library, newest first, or searched with `q`; filtered by date and source
  (report library PR, owner decisions 3 and 4, ADR-0432, ADR-0436);
- GET /v1/reports/{id}: the report and its versions;
- GET /v1/conversations/{conversation_id}/reports: the conversation's reports;
- GET /v1/reports/{id}/versions/{n}: one version's metadata and stored page (the panel's code view);
- GET /v1/reports/{id}/versions/{n}/page?form=view|download: the page as it leaves: `view` links the kit
  from `kit_base` (the gateway's kit path, the page's own origin, I4), `download` inlines the kit so the
  file opens offline with nothing to fetch (I6)."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response

from app.api.routes import _authorized, _caller, _error
from app.api.schemas import (
    CONVERSATION_ID_PATTERN,
    ErrorEnvelope,
    LibraryItem,
    LibraryPage,
    ReportList,
    ReportVersionView,
    ReportView,
)
from app.rag.retrieve import query_terms
from app.artifacts.document import served, without_model_info
from app.artifacts.store import ReportUnavailable
from app.memory.types import MemoryScope

router = APIRouter(prefix="/v1", tags=["reports"])

REPORT_ID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
# The kit's path on the page's own origin, as the gateway serves it: scheme, host, optional port, the path.
KIT_BASE_PATTERN = r"^https?://[A-Za-z0-9.\-]+(:[0-9]{1,5})?/report-kit/v[0-9]+/$"
_ERRORS = {
    400: {"model": ErrorEnvelope},
    401: {"model": ErrorEnvelope},
    404: {"model": ErrorEnvelope, "description": "report_not_found: no such report of the caller's"},
    503: {"model": ErrorEnvelope, "description": "report_unavailable: the page store did not answer in time"},
}
Caller = Annotated[MemoryScope, Depends(_caller)]
ReportId = Annotated[str, Path(pattern=REPORT_ID_PATTERN)]


def _refused(request: Request, request_id: str):
    services = request.app.state.services
    if not _authorized(request.headers.get("x-internal-token"), services.settings.agent_internal_token):
        return _error(request, request_id, "unauthorized", 401)
    if services.reports is None:
        return _error(request, request_id, "report_not_found", 404)
    return None


SOURCE_PATTERN = r"^(general|file|kb:[a-z0-9][a-z0-9-]{0,62})$"


@router.get("/reports", response_model=LibraryPage, responses=_ERRORS)
async def library(
    request: Request,
    caller: Caller,
    q: str = Query(default="", max_length=500, description="Words to search for; empty lists the newest."),
    since: datetime | None = Query(default=None, description="Changed on or after this time."),
    until: datetime | None = Query(default=None, description="Changed before this time."),
    source: str | None = Query(default=None, pattern=SOURCE_PATTERN),
    before: datetime | None = Query(default=None, description="`next_before` of the previous page."),
    limit: int = Query(default=30, ge=1, le=100),
    x_request_id: str | None = Header(default=None),
):
    """The caller's own reports only (owner in the store's every WHERE, ADR-0432); the gateway checks each
    returned conversation's owner again."""
    request_id = x_request_id or str(uuid.uuid4())
    if (refused := _refused(request, request_id)) is not None:
        return refused
    services = request.app.state.services
    config = services.library.search
    terms = query_terms(q, config.stopwords.all())[: config.max_query_terms] if q.strip() else []
    size = min(limit, config.page_size if not terms else limit)
    hits = await services.reports.search(
        caller.tenant_id, caller.user_id, terms, limit=size + 1, since=since, until=until, source=source,
        before=before if not terms else None,
    )
    page = hits[:size]
    return LibraryPage(
        reports=[
            LibraryItem(
                id=h.report_id, conversation_id=h.conversation_id, title=h.title, latest_version=h.latest_version,
                matched_version=h.version, subject=h.subject,
                sections=[str(s.get("title")) for s in h.sections if s.get("title")][:30],
                sources=h.sources[:20], source_kinds=h.source_kinds, created_at=h.created_at, updated_at=h.updated_at,
            )
            for h in page
        ],
        next_before=page[-1].updated_at if len(hits) > size and not terms else None,
    )


@router.get("/reports/{report_id}", response_model=ReportView, responses=_ERRORS)
async def get_report(
    request: Request, report_id: ReportId, caller: Caller, x_request_id: str | None = Header(default=None)
):
    request_id = x_request_id or str(uuid.uuid4())
    if (refused := _refused(request, request_id)) is not None:
        return refused
    record = await request.app.state.services.reports.get(caller.tenant_id, caller.user_id, report_id)
    if record is None:
        return _error(request, request_id, "report_not_found", 404)
    return ReportView.model_validate(record.model_dump())


@router.get("/conversations/{conversation_id}/reports", response_model=ReportList, responses=_ERRORS)
async def list_reports(
    request: Request,
    caller: Caller,
    conversation_id: str = Path(pattern=CONVERSATION_ID_PATTERN),
    x_request_id: str | None = Header(default=None),
):
    request_id = x_request_id or str(uuid.uuid4())
    if (refused := _refused(request, request_id)) is not None:
        return refused
    records = await request.app.state.services.reports.in_conversation(caller.tenant_id, caller.user_id, conversation_id)
    return ReportList(reports=[ReportView.model_validate(r.model_dump()) for r in records])


async def _version(request: Request, request_id: str, caller: MemoryScope, report_id: str, version: int):
    services = request.app.state.services
    record = await services.reports.version(caller.tenant_id, caller.user_id, report_id, version)
    if record is None:
        return None, _error(request, request_id, "report_not_found", 404)
    try:
        page = await services.reports.page(record, services.artifacts.report.storage.store_s)
    except ReportUnavailable:
        return None, _error(request, request_id, "report_unavailable", 503)
    text = page.decode("utf-8")
    # Owner decision C (ADR-0391): the model strip never leaves when the deployment's setting is false.
    return (record, text if services.artifacts.show_model_info else without_model_info(text)), None


@router.get("/reports/{report_id}/versions/{version}", response_model=ReportVersionView, responses=_ERRORS)
async def get_version(
    request: Request,
    report_id: ReportId,
    caller: Caller,
    version: int = Path(ge=1, le=100_000),
    x_request_id: str | None = Header(default=None),
):
    request_id = x_request_id or str(uuid.uuid4())
    if (refused := _refused(request, request_id)) is not None:
        return refused
    found, error = await _version(request, request_id, caller, report_id, version)
    if error is not None:
        return error
    record, page = found
    return ReportVersionView(
        id=record.report_id,
        conversation_id=record.conversation_id,
        version=record.version,
        title=record.title,
        size=record.size,
        locale=record.locale,
        created_at=record.created_at,
        figures=record.figures,
        general=record.general,
        citations=record.citations,
        html=page,
    )


@router.get(
    "/reports/{report_id}/versions/{version}/page",
    response_class=Response,
    responses={200: {"content": {"text/html": {"schema": {"type": "string"}}}}, **_ERRORS},
)
async def get_page(
    request: Request,
    report_id: ReportId,
    caller: Caller,
    version: int = Path(ge=1, le=100_000),
    form: Literal["view", "download"] = Query(default="view"),
    kit_base: str = Query(default="", max_length=300, pattern=r"^$|" + KIT_BASE_PATTERN),
    x_request_id: str | None = Header(default=None),
):
    request_id = x_request_id or str(uuid.uuid4())
    if (refused := _refused(request, request_id)) is not None:
        return refused
    if form == "view" and not kit_base:
        return _error(request, request_id, "invalid_request", 400)
    found, error = await _version(request, request_id, caller, report_id, version)
    if error is not None:
        return error
    _, page = found
    kit = request.app.state.services.artifacts.kit
    return Response(served(page, kit, form=form, kit_base=kit_base), media_type="text/html; charset=utf-8")
