"""The platform settings in force: the files' values with the administrator's overrides written in
(owner decision 2026-10-22, ADR-0276).

`Defaults` is built once at startup from the files, which are validated as before. `Effective` is
one immutable snapshot of what a request uses: the turn's deadline and limits, the model,
embedding and rerank ceilings, the upload limits, and each MCP server's max_concurrency. The store
(app/platform_settings/store.py) keeps the newest one; every request pins the snapshot current
when it starts (`pin`), and the work it starts inherits it (a ContextVar), so a change applies to
requests that start after it and a request in flight keeps its values (ADR-0279). Code that reads a
setting calls `turn(default)`, `timeouts(default)` and so on: the pinned value, or the file's
default when nothing is pinned (a test, a tool run outside a request)."""

import copy
import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any

from app.attachments.limits import UploadLimits
from app.models.config import Timeouts
from app.resilience import budget
from app.resilience.limits import Caps
from app.resilience.turn import TurnConfig

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Defaults:
    """The files' values: the budget rules' values dict and the objects built from it."""

    values: dict
    turn: TurnConfig
    timeouts: Timeouts
    upload: UploadLimits
    register_response_s: float
    uploads_per_minute: int
    mcp_max_concurrency: Mapping[str, int]
    lock_pool_size: int
    # Where each redeploy-only value is set (config/deploy-values.yaml `sources`), for the page.
    deploy_sources: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Effective:
    """`version`: the stored version in force, 0 for none (the files' values). `ignored`: the
    stored version failed validation and the files' values are in force instead (its problems are
    logged and shown); `overrides` is then empty. The `*_updates` are what the overrides change in
    each configuration object; `turn`, `timeouts` and `upload` are those objects built over the
    files' values (the settings page shows them)."""

    version: int
    overrides: Mapping[str, Any]
    values: dict
    turn: TurnConfig
    timeouts: Timeouts
    upload: UploadLimits
    register_response_s: float
    uploads_per_minute: int
    mcp_max_concurrency: Mapping[str, int]
    lock_pool_size: int
    turn_updates: Mapping[str, Any] = field(default_factory=dict)
    timeouts_updates: Mapping[str, Any] = field(default_factory=dict)
    upload_updates: Mapping[str, Any] = field(default_factory=dict)
    register_updated: bool = False
    ignored: tuple[str, ...] = field(default_factory=tuple)
    _over: dict = field(default_factory=dict, compare=False, repr=False)

    def turn_caps(self) -> Caps:
        """The turn limits over the platform's capacity (LOCK_POOL_SIZE, ADR-0262)."""
        return Caps(
            per_user=self.turn.per_user_max,
            per_tenant=self.turn.per_tenant_max,
            tenant_overrides=dict(self.turn.per_tenant_overrides),
            platform=self.lock_pool_size,
        )

    def upload_caps(self) -> Caps:
        """The registrations in flight (ADR-0265)."""
        upload = self.upload
        return Caps(
            per_user=upload.max_concurrent_uploads,
            per_tenant=upload.max_concurrent_uploads_per_tenant,
            tenant_overrides=dict(upload.tenant_concurrent_uploads),
            platform=upload.max_concurrent_uploads_total,
        )

    def over(self, default: Any, updates: Mapping[str, Any], *, merge: bool = False) -> Any:
        """`default` with `updates` written in, built once per default object; with `merge`, an
        update that is a map (some roles' ceilings) is merged into the default's map. A default
        these updates do not fit (a test's own short configuration) is used as it is, logged."""
        if not updates:
            return default
        key = id(default)
        hit = self._over.get(key)
        if hit is not None and hit[0] is default:
            return hit[1]
        dumped = default.model_dump()
        merged = {**dumped, **{k: _merge(dumped.get(k), v) if merge else v for k, v in updates.items()}}
        try:
            built = type(default).model_validate(merged)
        except ValueError as exc:
            log.warning("platform settings do not fit a %s in use: %s", type(default).__name__, exc)
            built = default
        self._over[key] = (default, built)
        return built


def _merge(default: Any, update: Any) -> Any:
    """A partial map (some roles' ceilings) over the default's map; any other value replaces it."""
    if isinstance(default, dict) and isinstance(update, dict):
        return {**default, **update}
    return update


def build(defaults: Defaults, version: int, overrides: Mapping[str, Any], ignored=()) -> Effective:
    """The snapshot `overrides` produce over `defaults`. The caller has checked them with
    budget.evaluate; the models here validate them again, so a snapshot is always consistent."""
    overrides = copy.deepcopy(dict(overrides))
    values = budget.apply(defaults.values, overrides)
    turn_updates = {
        key.split(".", 1)[1]: value for key, value in overrides.items() if key.split(".", 1)[0] == "turn"
    }
    timeouts = values["models"]["timeouts"]
    timeouts_updates: dict[str, Any] = {}
    for key, roles in (
        ("models.helper_s", budget.HELPER_ROLES),
        ("models.reasoning_s", budget.REASONING_ROLES),
    ):
        if key in overrides:
            timeouts_updates.setdefault("roles", {}).update({role: timeouts["roles"][role] for role in roles})
    for key, part in (("models.embed_query_s", "query"), ("models.embed_batch_s", "ingest")):
        if key in overrides:
            timeouts_updates.setdefault("embedding", {})[part] = timeouts["embedding"][part]
    if "models.rerank_s" in overrides:
        timeouts_updates["rerank"] = timeouts["rerank"]
    upload_updates = {
        key.split(".", 1)[1]: value
        for key, value in overrides.items()
        if key
        in (
            "docs.max_concurrent_uploads",
            "docs.max_concurrent_uploads_per_tenant",
            "docs.tenant_concurrent_uploads",
        )
    }
    limits = values["docs"]["limits"]
    servers = {s["id"]: int(s["max_concurrency"]) for s in values["mcp"]["servers"]}
    snapshot = Effective(
        version=version,
        overrides=overrides,
        values=values,
        turn=defaults.turn,
        timeouts=defaults.timeouts,
        upload=defaults.upload,
        register_response_s=float(values["docs"]["register_response_s"]),
        uploads_per_minute=int(limits["uploads_per_minute"]),
        mcp_max_concurrency={
            sid: min(n, defaults.mcp_max_concurrency.get(sid, n)) for sid, n in servers.items()
        },
        lock_pool_size=defaults.lock_pool_size,
        turn_updates=turn_updates,
        timeouts_updates=timeouts_updates,
        upload_updates=upload_updates,
        register_updated="docs.register_response_s" in overrides,
        ignored=tuple(ignored),
    )
    # Built over the files' values once, so a bad combination fails here, at save or load.
    turn = TurnConfig.model_validate({**defaults.turn.model_dump(), **turn_updates})
    built_timeouts = Timeouts.model_validate(
        {
            **defaults.timeouts.model_dump(),
            **{k: _merge(defaults.timeouts.model_dump().get(k), v) for k, v in timeouts_updates.items()},
        }
    )
    upload = UploadLimits.model_validate({**defaults.upload.model_dump(), **upload_updates})
    object.__setattr__(snapshot, "turn", turn)
    object.__setattr__(snapshot, "timeouts", built_timeouts)
    object.__setattr__(snapshot, "upload", upload)
    return snapshot


def from_files(defaults: Defaults, ignored=()) -> Effective:
    return build(defaults, 0, {}, ignored)


# ------------------------------------------------------------------ the snapshot a request uses

_PINNED: ContextVar[Effective | None] = ContextVar("platform_settings", default=None)


def pinned() -> Effective | None:
    return _PINNED.get()


def pin_here(snapshot: Effective | None) -> Token:
    """Pins `snapshot` for the rest of the current task and the tasks it starts."""
    return _PINNED.set(snapshot)


@contextmanager
def pin(snapshot: Effective | None) -> Iterator[None]:
    token = _PINNED.set(snapshot)
    try:
        yield
    finally:
        _PINNED.reset(token)


def turn(default: TurnConfig) -> TurnConfig:
    """The turn configuration this request uses: `default` (the caller's, usually the files') with
    the pinned overrides written in."""
    snapshot = _PINNED.get()
    return default if snapshot is None else snapshot.over(default, snapshot.turn_updates)


def timeouts(default: Timeouts) -> Timeouts:
    snapshot = _PINNED.get()
    return default if snapshot is None else snapshot.over(default, snapshot.timeouts_updates, merge=True)


def upload(default: UploadLimits) -> UploadLimits:
    snapshot = _PINNED.get()
    return default if snapshot is None else snapshot.over(default, snapshot.upload_updates)


def register_response_s(default: float) -> float:
    snapshot = _PINNED.get()
    return snapshot.register_response_s if snapshot is not None and snapshot.register_updated else default
