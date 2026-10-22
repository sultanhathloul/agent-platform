"""Admin API for the platform settings (owner decision 2026-10-22, ADR-0276 to ADR-0283), internal:
only the gateway calls it, after checking the caller's `admin` role and before auditing the call.

The agent checks the role too (item 5, ADR-0284): the gateway's admin proxy sends
X-User-Admin: true, from the same verified token, on admin calls only, and every endpoint here
refuses a call without it (403 forbidden), so a gateway route that forwarded a user's call here by
mistake is still refused. The caller is the actor of every change, with the username and display
name the gateway read from the token (ADR-0123).

GET reads the files' defaults, the overrides in force, the read-only values and the timeout tree;
POST preview checks a draft and shows what it would produce; POST saves a draft as the next version,
POST revert brings an earlier version's overrides back as the next version, GET versions lists the
history. GET usage reads the process's counters only (ADR-0282). GET /v1/internal/settings/limits is
the gateway's own read of the values it enforces, without a caller (ADR-0283)."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse

from app.api.admin import _decoded
from app.api.routes import _authorized
from app.api.schemas import (
    EffectiveLimits,
    ErrorEnvelope,
    PlatformSettingsView,
    PlatformUsage,
    ReadOnlySetting,
    SettingChange,
    SettingField,
    SettingsDraft,
    SettingsPreview,
    SettingsProblem,
    SettingsRevert,
    SettingsSaved,
    SettingsVersions,
    SettingsVersionView,
    TimeoutPair,
)
from app.guardrails.store import Actor
from app.platform_settings.effective import Effective
from app.platform_settings.messages import SettingsMessages, family, params
from app.platform_settings.store import SettingsRejected, SettingsStore, VersionConflict, VersionNotFound
from app.platform_settings.usage import usage
from app.resilience import budget

router = APIRouter(prefix="/v1/admin", tags=["admin-settings"])
internal = APIRouter(prefix="/v1/internal", tags=["internal"])

_ERRORS = {
    400: {"model": ErrorEnvelope, "description": "invalid_request, or settings_invalid with the conflicts"},
    401: {"model": ErrorEnvelope},
    403: {"model": ErrorEnvelope, "description": "forbidden: the caller is not an admin (X-User-Admin)"},
}


class SettingsCall:
    def __init__(self, request: Request, actor: Actor, request_id: str):
        self.request, self.actor, self.request_id = request, actor, request_id
        self.services = request.app.state.services

    @property
    def store(self) -> SettingsStore:
        return self.services.platform

    def error(
        self,
        code: str,
        status: int,
        details: list[str] | None = None,
        *,
        message: tuple[str, str] | None = None,
    ) -> JSONResponse:
        envelope = ErrorEnvelope.of(self.request_id, self.services.messages.error(code), details)
        if message is not None:
            envelope.error.message_ar, envelope.error.message_en = message
        return JSONResponse(envelope.model_dump(exclude_none=True), status_code=status)


async def _admin(
    request: Request,
    x_user_id: Annotated[str, Header(min_length=1, max_length=256, description="The admin making the call.")],
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
    actor = Actor(x_user_id, _decoded(x_user_name), _decoded(x_user_display_name))
    call = SettingsCall(request, actor, x_request_id or str(uuid.uuid4()))
    if not _authorized(x_internal_token, call.services.settings.agent_internal_token):
        return call.error("unauthorized", 401)
    if (x_user_admin or "").strip().lower() != "true":
        return call.error("forbidden", 403)
    return call


Admin = Annotated[SettingsCall | JSONResponse, Depends(_admin)]


# ------------------------------------------------------------------ views


def _names(services):
    """A capability's name and a tool's label, in Arabic and English, for the plain-words messages."""

    def names(kind: str, ident: str) -> tuple[str, str]:
        if kind == "capability":
            try:
                name = services.registry.get(ident).manifest.name
                return name.ar, name.en
            except Exception:
                return "إحدى القدرات", ident
        if kind == "tool" and services.tools.has(ident):
            spec = services.tools.get(ident)
            if spec.label is not None:
                return spec.label.ar, spec.label.en
            return spec.description_ar, spec.description_en
        return "إحدى الأدوات", ident

    return names


def _fields(store: SettingsStore, values: dict, overrides: dict) -> list[SettingField]:
    defaults = store.defaults.values
    out = []
    for setting in budget.editable(defaults):
        server = setting.key.split(".")[1] if setting.group == "mcp" else None
        out.append(
            SettingField(
                key=setting.key,
                group=setting.group,
                unit=setting.unit,
                integer=setting.integer,
                nullable=setting.nullable,
                per_tenant=setting.per_tenant,
                minimum=setting.minimum,
                maximum=setting.maximum(defaults),
                default=setting.read(defaults),
                value=setting.read(values),
                overridden=setting.key in overrides,
                source=setting.source,
                server=server,
            )
        )
    return out


def _tree(values: dict) -> list[TimeoutPair]:
    """The nested timeout pairs, in the rules' order (a margin the code must use is no pair)."""
    return [
        TimeoutPair(
            rule=check.name,
            family=family(check.name),
            params=params(check.name),
            inner=check.inner,
            inner_value=check.inner_v,
            outer=check.outer,
            outer_value=check.outer_v,
            strict=check.strict,
            holds=check.holds,
        )
        for check in budget.checks(values)
        if check.unit == "s"
    ]


def _read_only(services, store: SettingsStore) -> list[ReadOnlySetting]:
    """What only a redeploy changes, with where it is set (item 2)."""
    values = store.defaults.values
    deploy = values["gateway"]
    sandbox = values["docs"]["ingest"]["sandbox"]
    sources = store.defaults.deploy_sources
    return [
        ReadOnlySetting(
            key="lock_pool_size",
            value=store.defaults.lock_pool_size,
            unit="count",
            source=".env LOCK_POOL_SIZE (docker-compose.yml)",
        ),
        ReadOnlySetting(
            key="mem_limit.ingest",
            value=values["mem_limits_mb"]["ingest"],
            unit="MiB",
            source=sources.get("mem_limits_mb.ingest", "docker-compose.yml"),
        ),
        ReadOnlySetting(
            key="mem_limit.mcp-rag-ref",
            value=values["mem_limits_mb"]["mcp-rag-ref"],
            unit="MiB",
            source=sources.get("mem_limits_mb.mcp-rag-ref", "docker-compose.yml"),
        ),
        ReadOnlySetting(
            key="sandbox.workers",
            value=int(values["docs"]["ingest"]["workers"]),
            unit="count",
            source="config/docs.yaml ingest.workers",
        ),
        ReadOnlySetting(
            key="sandbox.memory_mb",
            value=sandbox["memory_mb"],
            unit="MiB",
            source="config/docs.yaml ingest.sandbox.memory_mb",
        ),
        ReadOnlySetting(
            key="sandbox.cpu_seconds",
            value=sandbox["cpu_seconds"],
            unit="cpu_s",
            source="config/docs.yaml ingest.sandbox.cpu_seconds",
        ),
        ReadOnlySetting(
            key="sandbox.timeout_seconds",
            value=sandbox["timeout_seconds"],
            unit="s",
            source="config/docs.yaml ingest.sandbox.timeout_seconds",
        ),
        ReadOnlySetting(
            key="sandbox.parent_reserve_mb",
            value=sandbox["parent_reserve_mb"],
            unit="MiB",
            source="config/docs.yaml ingest.sandbox.parent_reserve_mb",
        ),
        ReadOnlySetting(
            key="turn.gateway_margin_s",
            value=values["turn"]["gateway_margin_s"],
            unit="s",
            source="config/turn.yaml gateway_margin_s",
        ),
        ReadOnlySetting(
            key="keep_alive.gateway_s",
            value=deploy["keep_alive_s"],
            unit="s",
            source=sources.get("gateway.keep_alive_s", "application.yml"),
        ),
        ReadOnlySetting(
            key="keep_alive.agent_s",
            value=values["agent_keep_alive_s"],
            unit="s",
            source=sources.get("agent_keep_alive_s", "services/agent/Dockerfile"),
        ),
    ]


def _view(call: SettingsCall, snapshot: Effective) -> PlatformSettingsView:
    store = call.store
    return PlatformSettingsView(
        version=snapshot.version,
        ignored=list(snapshot.ignored),
        fields=_fields(store, snapshot.values, dict(snapshot.overrides)),
        read_only=_read_only(call.services, store),
        tree=_tree(snapshot.values),
    )


def _problems(call: SettingsCall, problems: list[budget.Check]) -> list[SettingsProblem]:
    messages: SettingsMessages = call.store.messages
    names = _names(call.services)
    known = {s.key: s for s in budget.editable(call.store.defaults.values)}
    out = []
    for problem in problems:
        found = params(problem.name)
        minimum = known[found["key"]].minimum if found.get("key") in known else None
        ar, en = messages.describe(problem, names, minimum)
        out.append(
            SettingsProblem(
                rule=problem.name,
                family=family(problem.name),
                params=found,
                inner_value=problem.inner_v,
                outer_value=problem.outer_v,
                minimum=minimum,
                message_ar=ar,
                message_en=en,
            )
        )
    return out


def _refused(call: SettingsCall, problems: list[budget.Check]) -> JSONResponse:
    """400 settings_invalid: the conflicts in plain words, each rule's id in details."""
    described = _problems(call, problems)
    message = (" ".join(p.message_ar for p in described), " ".join(p.message_en for p in described))
    return call.error("settings_invalid", 400, [p.rule for p in described], message=message)


def _changes(before: dict, after: dict) -> list[SettingChange]:
    return [
        SettingChange(key=key, before=before[key], after=after[key])
        for key in before
        if before[key] != after.get(key)
    ]


def _overrides(value: Any) -> dict[str, Any]:
    return dict(value or {})


# ------------------------------------------------------------------ endpoints


@router.get("/settings", response_model=PlatformSettingsView, responses=_ERRORS)
async def get_settings(call: Admin):
    if isinstance(call, JSONResponse):
        return call
    return _view(call, call.store.current)


@router.post("/settings/preview", response_model=SettingsPreview, responses=_ERRORS)
async def preview_settings(body: SettingsDraft, call: Admin):
    """What the draft would produce: every field's value, the timeout tree on those values, and why
    it cannot be saved, if it cannot. Nothing is written."""
    if isinstance(call, JSONResponse):
        return call
    store = call.store
    overrides = _overrides(body.overrides)
    values, problems = budget.evaluate(store.defaults.values, overrides)
    current = budget.defaults(store.current.values)
    after = budget.defaults(values)
    return SettingsPreview(
        valid=not problems,
        problems=_problems(call, problems),
        fields=_fields(store, values, overrides),
        tree=_tree(values) if not any(family(p.name) in ("range", "unknown") for p in problems) else [],
        changed=[c.key for c in _changes(current, after)],
    )


async def _save(call: SettingsCall, overrides: dict, *, expected: int | None, reverted_to: int | None):
    store = call.store
    before_values = budget.defaults(store.current.values)
    previous = dict(store.current.overrides)
    if reverted_to is not None:
        change = "revert"
    elif set(overrides) < set(previous) and all(overrides[k] == previous[k] for k in overrides):
        change = "restore"  # only removals: one or more values back to their default
    else:
        change = "save"
    try:
        _, created = await store.save(
            overrides, call.actor, change=change, reverted_to=reverted_to, expected_version=expected
        )
    except SettingsRejected as rejected:
        return _refused(call, rejected.problems)
    except VersionConflict as conflict:
        return call.error("settings_conflict", 409, [f"latest_version: {conflict.latest}"])
    snapshot = store.current
    return JSONResponse(
        SettingsSaved(
            version=created.version,
            change=change,
            reverted_to=reverted_to,
            changes=_changes(before_values, budget.defaults(snapshot.values)),
            settings=_view(call, snapshot),
        ).model_dump(mode="json"),
        status_code=201,
    )


@router.post(
    "/settings",
    response_model=SettingsSaved,
    status_code=201,
    responses={
        **_ERRORS,
        409: {"model": ErrorEnvelope, "description": "settings_conflict: another save came first"},
    },
)
async def save_settings(body: SettingsDraft, call: Admin):
    """Saves the draft's overrides as the next version, when every rule accepts them; refused with
    400 settings_invalid otherwise, and nothing changes. Applies to requests that start after it."""
    if isinstance(call, JSONResponse):
        return call
    return await _save(call, _overrides(body.overrides), expected=body.expected_version, reverted_to=None)


@router.post(
    "/settings/revert",
    response_model=SettingsSaved,
    status_code=201,
    responses={
        **_ERRORS,
        404: {"model": ErrorEnvelope, "description": "settings_version_not_found"},
        409: {"model": ErrorEnvelope, "description": "settings_conflict: another save came first"},
    },
)
async def revert_settings(body: SettingsRevert, call: Admin):
    """Brings an earlier version's overrides back as a new version (nothing is deleted); refused
    like a save when they no longer pass the rules."""
    if isinstance(call, JSONResponse):
        return call
    try:
        earlier = await call.store.get(body.version)
    except VersionNotFound:
        return call.error("settings_version_not_found", 404)
    return await _save(
        call, dict(earlier.content), expected=body.expected_version, reverted_to=earlier.version
    )


@router.get("/settings/versions", response_model=SettingsVersions, responses=_ERRORS)
async def settings_versions(call: Admin, limit: Annotated[int, Query(ge=1, le=200)] = 50):
    if isinstance(call, JSONResponse):
        return call
    return SettingsVersions(
        versions=[
            SettingsVersionView(
                version=v.version,
                change=v.change,
                reverted_to=v.reverted_to,
                overrides=v.content,
                created_at=v.created_at,
                created_by=v.created_by,
                created_by_username=v.created_by_username,
                created_by_name=v.created_by_name,
            )
            for v in await call.store.versions(limit)
        ]
    )


@router.get("/usage", response_model=PlatformUsage, responses=_ERRORS)
async def platform_usage(call: Admin):
    """What is in use now, from the process's counters only: no database read, counts only."""
    if isinstance(call, JSONResponse):
        return call
    return PlatformUsage.model_validate(usage(call.services))


@internal.get("/settings/limits", response_model=EffectiveLimits, responses={401: {"model": ErrorEnvelope}})
async def effective_limits(
    request: Request,
    x_internal_token: Annotated[str | None, Header()] = None,
    x_request_id: Annotated[str | None, Header()] = None,
):
    """The values the gateway enforces itself, in force now; it reads them at most every 10 s and
    keeps the last ones it read when this cannot be reached (ADR-0283)."""
    services = request.app.state.services
    if not _authorized(x_internal_token, services.settings.agent_internal_token):
        envelope = ErrorEnvelope.of(
            x_request_id or str(uuid.uuid4()), services.messages.error("unauthorized")
        )
        return JSONResponse(envelope.model_dump(), status_code=401)
    snapshot = services.platform.current
    return EffectiveLimits(
        version=snapshot.version,
        deadline_s=snapshot.turn.deadline_s,
        uploads_per_minute=snapshot.uploads_per_minute,
        max_concurrent_uploads=snapshot.upload.max_concurrent_uploads,
    )
