"""Platform settings overrides in Postgres, cached in Redis, reloaded on change (owner decision
2026-10-22, ADR-0276), in the guard policies' pattern (app/guardrails/store.py).

`agent.platform_settings` holds versions of one document of overrides; the newest is in force. A
save, a revert and a restore each insert the next version (append-only, ADR-0166); nothing is
updated or deleted. Every process keeps the snapshot in force in memory (`current`), which is what
requests read: never the database per request. A change is written to Postgres, the Redis cache
key is rewritten, and a message on the change channel makes every process (the agent, the ingest
service) reload from that cache. A process also reloads from Postgres when it (re)subscribes and
every `refresh_s`, so a lost message heals. When Redis or Postgres cannot be read, the last known
snapshot stays in force (ADR-0279).

A stored version is checked against the files' values every time it is loaded, with the rules a
save is checked with (app/resilience/budget.py): one that no longer passes (a file default changed
under it) is ignored, the files' values are used, and an error is logged naming the rules it fails
(ADR-0280)."""

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb

from app.db import Database
from app.guardrails.store import SEED_ACTOR, Actor
from app.platform_settings.effective import Defaults, Effective, build, from_files
from app.platform_settings.messages import SettingsMessages
from app.resilience import budget

log = logging.getLogger(__name__)


class SettingsRejected(ValueError):
    """Overrides the rules refuse; nothing was written."""

    def __init__(self, problems: list[budget.Check]):
        super().__init__("; ".join(p.detail for p in problems))
        self.problems = problems


class VersionNotFound(LookupError):
    pass


@dataclass(frozen=True)
class Version:
    version: int
    content: dict[str, Any]
    change: str
    reverted_to: int | None
    created_at: datetime
    created_by: str
    created_by_username: str | None
    created_by_name: str | None


def _version(row: dict) -> Version:
    content = row["content"] if isinstance(row["content"], dict) else json.loads(row["content"])
    return Version(
        row["version"],
        content,
        row["change"],
        row["reverted_to"],
        row["created_at"],
        row["created_by"],
        row["created_by_username"],
        row["created_by_name"],
    )


_SELECT = (
    "SELECT version, content, change, reverted_to, created_at, created_by, created_by_username, "
    "created_by_name FROM agent.platform_settings"
)


async def _lock(conn) -> None:
    """Saves are serialized, so each is checked against the version it follows. Transaction-scoped;
    the two-key form, apart from the conversation locks' one-key space (app/api/locks.py)."""
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext('agent'), hashtext('platform_settings'))")


class SettingsStore:
    def __init__(
        self,
        db: Database,
        defaults: Defaults,
        redis: Any = None,
        *,
        namespace: str = "agent",
        refresh_s: float = 30.0,
        messages: SettingsMessages | None = None,
    ):
        self._db = db
        self.defaults = defaults
        # Why a save is refused, in plain words (config/messages/settings.yaml); the agent's only.
        self.messages = messages
        self._redis = redis
        self._cache_key = f"{namespace}:platform:settings"
        self._channel = f"{namespace}:platform:settings:changed"
        self._refresh_s = refresh_s
        self._current = from_files(defaults)
        self._hooks: list[Callable[[Effective], None]] = []
        self._tasks: list[asyncio.Task] = []

    # ------------------------------------------------------------------ what requests read

    @property
    def current(self) -> Effective:
        return self._current

    def on_change(self, hook: Callable[[Effective], None]) -> None:
        """`hook(snapshot)` runs at once and on every new snapshot (the shared limits it sizes)."""
        self._hooks.append(hook)
        hook(self._current)

    def _install(self, snapshot: Effective) -> None:
        if snapshot.version == self._current.version and snapshot.ignored == self._current.ignored:
            return
        self._current = snapshot
        for hook in self._hooks:
            try:
                hook(snapshot)
            except Exception:  # one hook's bug never keeps the others from the new values
                log.exception("platform settings hook failed")

    def snapshot(self, version: int, content: Mapping[str, Any]) -> Effective:
        """The snapshot a stored version gives; the files' values when it fails the rules."""
        _, problems = budget.evaluate(self.defaults.values, dict(content))
        if problems:
            names = [p.name for p in problems]
            log.error(
                "platform settings version %d fails validation against the files (%s): ignored, "
                "the files' values are in force",
                version,
                ", ".join(names),
            )
            return build(self.defaults, version, {}, ignored=names)
        return build(self.defaults, version, content)

    # ------------------------------------------------------------------ loading

    async def load(self) -> Effective:
        """Reloads the newest version from Postgres and rewrites the cache. Raises when Postgres
        cannot be read; the snapshot in force is kept."""
        async with self._db.connection() as conn:
            row = await (await conn.execute(_SELECT + " ORDER BY version DESC LIMIT 1")).fetchone()
        if row is not None:
            stored = _version(row)
            self._install(self.snapshot(stored.version, stored.content))
            await self._write_cache(stored.version, stored.content)
        return self._current

    async def _write_cache(self, version: int, content: Mapping[str, Any]) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.set(self._cache_key, json.dumps({"version": version, "content": content}))
        except Exception as exc:  # Postgres stays the source of truth
            log.warning("platform settings cache write failed: %s", type(exc).__name__)

    async def _reload_from_cache(self) -> None:
        try:
            raw = await self._redis.get(self._cache_key)
        except Exception as exc:
            log.warning(
                "platform settings cache read failed, reloading from Postgres: %s", type(exc).__name__
            )
            raw = None
        if raw is None:
            await self.load()
            return
        cached = json.loads(raw)
        if int(cached["version"]) != self._current.version:
            self._install(self.snapshot(int(cached["version"]), cached["content"]))

    async def _changed(self) -> None:
        """After a write: reload from Postgres, rewrite the cache, tell every process."""
        await self.load()
        if self._redis is not None:
            try:
                await self._redis.publish(self._channel, "changed")
            except Exception as exc:
                log.warning("platform settings change not published: %s", type(exc).__name__)

    # ------------------------------------------------------------------ lifecycle

    async def seed(self) -> bool:
        """Version 1, no overrides, by the seed, when the table is empty: the history starts with
        the files' values."""
        async with self._db.connection() as conn, conn.transaction():
            await _lock(conn)
            if await (await conn.execute("SELECT 1 FROM agent.platform_settings LIMIT 1")).fetchone():
                return False
            await self._insert(conn, 1, {}, "seed", None, SEED_ACTOR)
        log.info("seeded platform settings: version 1, the files' values")
        return True

    async def start(self, *, seed: bool = True) -> None:
        """Loads the version in force; `seed` (the agent, not the ingest service) writes version 1
        first when the table is empty."""
        if seed:
            await self.seed()
        await self.load()
        self._tasks = [asyncio.create_task(self._refresh())]
        if self._redis is not None:
            self._tasks.append(asyncio.create_task(self._listen()))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks = []

    async def _listen(self) -> None:
        while True:
            try:
                pubsub = self._redis.pubsub()
                await pubsub.subscribe(self._channel)
                await self.load()  # whatever changed while unsubscribed
                try:
                    async for message in pubsub.listen():
                        if message.get("type") == "message":
                            await self._reload_from_cache()
                finally:
                    await pubsub.aclose()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("platform settings listener lost Redis (%s); retrying", type(exc).__name__)
                await asyncio.sleep(1.0)

    async def _refresh(self) -> None:
        while True:
            await asyncio.sleep(self._refresh_s)
            try:
                await self.load()
            except Exception as exc:
                log.warning("periodic platform settings reload failed: %s", type(exc).__name__)

    # ------------------------------------------------------------------ admin reads

    async def versions(self, limit: int = 50) -> list[Version]:
        async with self._db.connection() as conn:
            rows = await (
                await conn.execute(_SELECT + " ORDER BY version DESC LIMIT %s", (limit,))
            ).fetchall()
        return [_version(r) for r in rows]

    async def get(self, version: int) -> Version:
        async with self._db.connection() as conn:
            row = await (await conn.execute(_SELECT + " WHERE version = %s", (version,))).fetchone()
        if row is None:
            raise VersionNotFound(version)
        return _version(row)

    # ------------------------------------------------------------------ admin writes

    async def save(
        self,
        overrides: Mapping[str, Any],
        actor: Actor,
        *,
        change: str = "save",
        reverted_to: int | None = None,
        expected_version: int | None = None,
    ) -> tuple[Version, Version]:
        """Inserts `overrides` as the next version, when the rules accept them; raises
        SettingsRejected and writes nothing otherwise. With `expected_version`, a save that
        follows a version other than the one the administrator previewed is refused
        (VersionConflict). Returns the version it follows and the new one."""
        content = dict(overrides)
        _, problems = budget.evaluate(self.defaults.values, content)
        if problems:
            raise SettingsRejected(problems)
        async with self._db.connection() as conn, conn.transaction():
            await _lock(conn)
            row = await (await conn.execute(_SELECT + " ORDER BY version DESC LIMIT 1")).fetchone()
            before = _version(row) if row is not None else None
            latest = before.version if before is not None else 0
            if expected_version is not None and expected_version != latest:
                raise VersionConflict(latest)
            created = await self._insert(conn, latest + 1, content, change, reverted_to, actor)
        await self._changed()
        empty = Version(0, {}, "seed", None, created.created_at, "files", None, None)
        return before or empty, created

    async def _insert(
        self, conn, version: int, content: dict, change: str, reverted_to: int | None, actor: Actor
    ) -> Version:
        row = await (
            await conn.execute(
                "INSERT INTO agent.platform_settings (version, content, change, reverted_to, created_by, "
                "created_by_username, created_by_name) VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "RETURNING version, content, change, reverted_to, created_at, created_by, "
                "created_by_username, created_by_name",
                (version, Jsonb(content), change, reverted_to, actor.id, actor.username, actor.name),
            )
        ).fetchone()
        return _version(row)


class VersionConflict(Exception):
    """Another save came first: the overrides were checked against an older version."""

    def __init__(self, latest: int):
        super().__init__(f"the newest version is {latest}")
        self.latest = latest


def changed_keys(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    return sorted(key for key in {*before, *after} if before.get(key, _ABSENT) != after.get(key, _ABSENT))


_ABSENT = object()


def defaults_and_effective(store: SettingsStore) -> tuple[dict, dict]:
    """Every editable setting's default and the value in force (the defaults where no override)."""
    defaults = budget.defaults(store.defaults.values)
    current = budget.defaults(store.current.values)
    return defaults, current


def keys(store: SettingsStore) -> Sequence[budget.Setting]:
    return budget.editable(store.defaults.values)
