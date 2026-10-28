"""Reports, internal API (reports PR, I5, I6, ADR-0373, ADR-0374). The caller is named by X-User-Id and
X-Tenant-Id, which the gateway sets from the verified token, never from client input (ADR-0059); every
read is the owner's only: a report of another user or tenant, like one that does not exist, is 404
`report_not_found`, so an id reveals nothing. The gateway checks the owner first; this is the second check.

- GET /v1/reports/{id}: the report and its versions;
- GET /v1/conversations/{conversation_id}/reports: the conversation's reports;
- GET /v1/reports/{id}/versions/{n}: one version's metadata and stored page (the panel's code view);
- GET /v1/reports/{id}/versions/{n}/page?form=view|download: the page as it leaves: `view` links the kit
  from `kit_base` (the gateway's kit path, the page's own origin, I4), `download` inlines the kit so the
  file opens offline with nothing to fetch (I6)."""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response

from app.api.routes import _authorized, _caller, _error
from app.api.schemas import CONVERSATION_ID_PATTERN, ErrorEnvelope, ReportList, ReportVersionView, ReportView
from app.artifacts.document import served
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
    return (record, page.decode("utf-8")), None


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
        unverified=record.unverified,
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
