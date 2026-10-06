"""Admin API for guard policies (README §10.4, phase 03 C3), internal: only the gateway
calls it, after checking the caller's `admin` role and before auditing the call
(ADR-0042). The agent stays role-blind (ADR-0046): it trusts the internal token and
records the caller from X-User-Id as the actor of every change.

The agent owns agent.guard_policies (I1). Every write reaches every agent process
within 2 s through the store's Redis notification (I3)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.api.routes import _authorized
from app.api.schemas import (
    POLICY_ID_PATH,
    ErrorEnvelope,
    PolicyDetail,
    PolicyDocument,
    PolicyEnabled,
    PolicyImportReport,
    PolicyList,
    PolicyMatchRow,
    PolicyMetrics,
    PolicyOverrides,
    PolicyTestRequest,
    PolicyTestResult,
    PolicyVersionView,
    PolicyView,
    PolicyYaml,
    SpanView,
)
from app.guardrails.panel import PanelError, try_policy
from app.guardrails.schema import Policy
from app.guardrails.store import (
    PolicyExists,
    PolicyFileError,
    PolicyNotFound,
    export_document,
    parse_policy_documents,
)
from app.guardrails.store import PolicyView as StoredView
from app.identity import Principal

router = APIRouter(prefix="/v1/admin/policies", tags=["admin-policies"])

_ERRORS = {
    400: {"model": ErrorEnvelope, "description": "invalid_request or policy_invalid (with details)"},
    401: {"model": ErrorEnvelope},
}


class AdminCall:
    def __init__(self, request: Request, actor: str, tenant: str, request_id: str):
        self.request, self.actor, self.tenant, self.request_id = request, actor, tenant, request_id
        self.services = request.app.state.services

    def error(self, code: str, status: int, details: list[str] | None = None) -> JSONResponse:
        envelope = ErrorEnvelope.of(self.request_id, self.services.messages.error(code), details)
        return JSONResponse(envelope.model_dump(exclude_none=True), status_code=status)


async def _admin(
    request: Request,
    x_user_id: Annotated[str, Header(min_length=1, max_length=256, description="The admin making the call.")],
    x_tenant_id: Annotated[str, Header(min_length=1, max_length=256, description="The admin's tenant.")],
    x_internal_token: Annotated[str | None, Header()] = None,
    x_request_id: Annotated[str | None, Header()] = None,
):
    call = AdminCall(request, x_user_id, x_tenant_id, x_request_id or str(uuid.uuid4()))
    if not _authorized(x_internal_token, call.services.settings.agent_internal_token):
        return call.error("unauthorized", 401)
    return call


Admin = Annotated[AdminCall | JSONResponse, Depends(_admin)]
PolicyId = Annotated[str, Path(pattern=POLICY_ID_PATH)]


def _document(policy: Policy) -> PolicyDocument:
    return PolicyDocument.model_validate(policy.model_dump(mode="json"))


def _view(view: StoredView) -> PolicyView:
    return PolicyView(
        policy=_document(view.policy),
        created_at=view.created_at,
        created_by=view.created_by,
        updated_at=view.updated_at,
        updated_by=view.updated_by,
        overrides=view.overrides,
        exception=view.exception,
    )


def _validated(call: AdminCall, document: PolicyDocument) -> Policy | JSONResponse:
    try:
        return Policy.model_validate(document.model_dump(exclude={"version"}, exclude_none=True))
    except ValidationError as exc:
        return call.error(
            "policy_invalid",
            400,
            [f"{'.'.join(map(str, e['loc'])) or 'policy'}: {e['msg']}" for e in exc.errors()],
        )


@router.get("", response_model=PolicyList, responses=_ERRORS)
async def list_policies(call: Admin):
    if isinstance(call, JSONResponse):
        return call
    return PolicyList(policies=[_view(v) for v in await call.services.policies.list_policies()])


@router.get("/export", response_model=PolicyYaml, responses=_ERRORS)
async def export_policies(call: Admin):
    """Every policy's current version and switch as one importable YAML document."""
    if isinstance(call, JSONResponse):
        return call
    views = await call.services.policies.list_policies()
    return PolicyYaml(yaml=export_document(v.policy for v in views))


@router.get("/metrics", response_model=PolicyMetrics, responses=_ERRORS)
async def policy_metrics(call: Admin, days: Annotated[int, Query(ge=1, le=90)] = 14):
    if isinstance(call, JSONResponse):
        return call
    rows = await call.services.policies.matches_per_day(days)
    return PolicyMetrics(days=days, rows=[PolicyMatchRow(**r) for r in rows])


@router.post("/import", response_model=PolicyImportReport, responses=_ERRORS)
async def import_policies(body: PolicyYaml, call: Admin):
    """All or nothing: an absent id is created, a changed one versioned, a switch alone
    switched, an identical one left alone."""
    if isinstance(call, JSONResponse):
        return call
    try:
        policies = parse_policy_documents(body.yaml, source="import")
    except PolicyFileError as exc:
        return call.error("policy_invalid", 400, [f"{e['id']}: {e['error']}" for e in exc.errors])
    report = await call.services.policies.import_policies(policies, actor=call.actor)
    return PolicyImportReport(
        created=report.created,
        versioned=report.versioned,
        switched=report.switched,
        unchanged=report.unchanged,
    )


@router.post("/overrides", response_model=PolicyOverrides, responses=_ERRORS)
async def preview_overrides(body: PolicyDocument, call: Admin):
    """What this draft would override if saved now (ADR-0105): the edit form shows it."""
    if isinstance(call, JSONResponse):
        return call
    policy = _validated(call, body)
    if isinstance(policy, JSONResponse):
        return policy
    found = await call.services.policies.overrides(policy)
    return PolicyOverrides(overrides=found.refs, exception=found.exception)


@router.post("/test", response_model=PolicyTestResult, responses={**_ERRORS, 404: {"model": ErrorEnvelope}})
async def test_policy(body: PolicyTestRequest, call: Admin):
    if isinstance(call, JSONResponse):
        return call
    if (body.policy_id is None) == (body.policy is None):
        return call.error("invalid_request", 400, ["give exactly one of policy_id or policy"])
    if body.policy is not None:
        policy = _validated(call, body.policy)
        if isinstance(policy, JSONResponse):
            return policy
    else:
        try:
            policy = (await call.services.policies.get(body.policy_id)).policy
        except PolicyNotFound:
            return call.error("policy_not_found", 404)
    principal = Principal(user_id=call.actor, tenant_id=call.tenant)
    try:
        tried = await try_policy(
            call.services.guard,
            policy,
            body.text,
            principal=principal,
            checkpoint=body.checkpoint,
            target=body.target,
        )
    except (PanelError, ValueError) as exc:
        return call.error("invalid_request", 400, [str(exc)])
    result, outcome = tried.result, tried.outcome
    return PolicyTestResult(
        policy_ref=policy.ref,
        checkpoint=tried.checkpoint.value,
        target=tried.target,
        action=tried.action,
        matched=bool(result and result.matched),
        score=result.score if result else 0.0,
        explanation=result.explanation if result else "",
        spans=[SpanView(**s.model_dump()) for s in (result.spans if result else [])],
        output_text=tried.output_text,
        message_ar=outcome.message_ar,
        message_en=outcome.message_en,
        error=tried.error,
    )


@router.post(
    "", status_code=201, response_model=PolicyView, responses={**_ERRORS, 409: {"model": ErrorEnvelope}}
)
async def create_policy(body: PolicyDocument, call: Admin):
    """Version 1 of a new id; 409 policy_exists when the id is taken."""
    if isinstance(call, JSONResponse):
        return call
    policy = _validated(call, body)
    if isinstance(policy, JSONResponse):
        return policy
    try:
        await call.services.policies.create(policy, actor=call.actor)
    except PolicyExists:
        return call.error("policy_exists", 409)
    return JSONResponse(
        _view(await call.services.policies.get(policy.id)).model_dump(mode="json"), status_code=201
    )


@router.get("/{policy_id}", response_model=PolicyDetail, responses={**_ERRORS, 404: {"model": ErrorEnvelope}})
async def get_policy(policy_id: PolicyId, call: Admin):
    if isinstance(call, JSONResponse):
        return call
    try:
        current = await call.services.policies.get(policy_id)
        versions = await call.services.policies.versions(policy_id)
    except PolicyNotFound:
        return call.error("policy_not_found", 404)
    return PolicyDetail(
        current=_view(current),
        versions=[
            PolicyVersionView(
                policy=_document(v.policy),
                created_at=v.created_at,
                created_by=v.created_by,
                overrides=v.overrides,
                exception=v.exception,
            )
            for v in versions
        ],
    )


@router.post(
    "/{policy_id}/versions",
    status_code=201,
    response_model=PolicyView,
    responses={**_ERRORS, 404: {"model": ErrorEnvelope}},
)
async def add_version(policy_id: PolicyId, body: PolicyDocument, call: Admin):
    """The body becomes the next version and the current one; versions are immutable (I2)."""
    if isinstance(call, JSONResponse):
        return call
    if body.id != policy_id:
        return call.error("policy_invalid", 400, [f"id: the body names {body.id}, the path {policy_id}"])
    policy = _validated(call, body)
    if isinstance(policy, JSONResponse):
        return policy
    try:
        await call.services.policies.add_version(policy, actor=call.actor)
    except PolicyNotFound:
        return call.error("policy_not_found", 404)
    return JSONResponse(
        _view(await call.services.policies.get(policy_id)).model_dump(mode="json"), status_code=201
    )


@router.put(
    "/{policy_id}/enabled", response_model=PolicyView, responses={**_ERRORS, 404: {"model": ErrorEnvelope}}
)
async def set_enabled(policy_id: PolicyId, body: PolicyEnabled, call: Admin):
    """Switches the policy on or off; no new version."""
    if isinstance(call, JSONResponse):
        return call
    try:
        await call.services.policies.set_enabled(policy_id, body.enabled, actor=call.actor)
    except PolicyNotFound:
        return call.error("policy_not_found", 404)
    return _view(await call.services.policies.get(policy_id))
