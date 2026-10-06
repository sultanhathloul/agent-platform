"""Guard policies in Postgres, cached in Redis, reloaded on change (README §10.1).

`agent.guard_policies` holds immutable versions (I2); `agent.guard_policy_heads`
names each policy's current version and whether it is on. The agent owns both;
the gateway only proxies admin calls (I1).

Every agent process keeps the enabled current versions in memory, which is what
the engine reads on every checkpoint. A change (create, new version, enable,
import) is written to Postgres, the Redis cache key is rewritten from Postgres,
and a message on the change channel makes every process reload from that cache
within the 2 s bound of I3. A process also reloads from Postgres when it
(re)subscribes and every `refresh_s`, so a lost message heals. Without Redis
(REDIS_URL unset) a process sees only its own changes until its next refresh.

Seeding reads `config/policies/*.yaml` and `capabilities/*/policies/*.yaml` and
inserts only ids absent from the table; an existing policy is never overwritten,
whatever the file says now (I2).
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from app.db import Database
from app.guardrails.schema import GuardEventRecord, Policy, is_exception, overridden

log = logging.getLogger(__name__)

SEED_ACTOR = "seed"


class PolicyExists(ValueError):
    pass


class PolicyNotFound(LookupError):
    pass


class PolicyFileError(ValueError):
    """A seed or import document that does not hold valid policies."""

    def __init__(self, errors: list[dict[str, str]]):
        super().__init__("; ".join(f"{e['id']}: {e['error']}" for e in errors))
        self.errors = errors


@dataclass(frozen=True)
class PolicyView:
    """A policy's current version, its switch, who changed it last, and what the version
    overrides as computed when it was saved (ADR-0105)."""

    policy: Policy
    created_at: datetime
    created_by: str
    updated_at: datetime
    updated_by: str
    overrides: list[str] = field(default_factory=list)
    exception: bool = False


@dataclass(frozen=True)
class PolicyVersion:
    policy: Policy
    created_at: datetime
    created_by: str
    overrides: list[str] = field(default_factory=list)
    exception: bool = False


@dataclass(frozen=True)
class Overrides:
    """What a policy overrides (refs of the wider policies of its group) and whether that
    makes it an exception (ADR-0105)."""

    refs: list[str]
    exception: bool


@dataclass
class ImportReport:
    created: list[str] = field(default_factory=list)
    versioned: list[str] = field(default_factory=list)
    switched: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)


def _policy(row: dict) -> Policy:
    content = row["content"] if isinstance(row["content"], dict) else json.loads(row["content"])
    return Policy.model_validate(
        {**content, "id": row["id"], "version": row["version"], "enabled": row["enabled"]}
    )


def _view(row: dict) -> PolicyView:
    return PolicyView(
        _policy(row),
        row["created_at"],
        row["created_by"],
        row["updated_at"],
        row["updated_by"],
        list(row["overrides"]),
        row["exception"],
    )


def parse_policy_documents(text: str, *, source: str = "document") -> list[Policy]:
    """Policies from YAML: one policy mapping, a list of them, or {policies: [...]}.
    Every policy is validated; any error rejects the whole document."""
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PolicyFileError([{"id": source, "error": f"not YAML: {exc}"}]) from exc
    if isinstance(raw, dict) and "policies" in raw:
        raw = raw["policies"]
    items = raw if isinstance(raw, list) else [raw]
    policies, errors = [], []
    for index, item in enumerate(items):
        ident = item.get("id", f"#{index}") if isinstance(item, dict) else f"#{index}"
        if not isinstance(item, dict):
            errors.append({"id": str(ident), "error": "a policy is a mapping"})
            continue
        if "version" in item:
            errors.append({"id": str(ident), "error": "version is assigned by the store, not by a document"})
            continue
        try:
            policies.append(Policy.model_validate(item))
        except ValidationError as exc:
            errors.append({"id": str(ident), "error": "; ".join(e["msg"] for e in exc.errors())})
    ids = [p.id for p in policies]
    errors += [{"id": i, "error": "appears twice"} for i in sorted({i for i in ids if ids.count(i) > 1})]
    if errors:
        raise PolicyFileError(errors)
    return policies


def export_document(policies: Iterable[Policy]) -> str:
    """The current versions as an importable YAML document (versions are not exported)."""
    body = {"policies": [p.model_dump(mode="json", exclude={"version"}, exclude_none=True) for p in policies]}
    return yaml.safe_dump(body, allow_unicode=True, sort_keys=False, width=110)


_SELECT_CURRENT = (
    "SELECT p.id, p.version, p.content, p.created_at, p.created_by, p.overrides, p.exception, "
    "h.enabled, h.updated_at, h.updated_by "
    "FROM agent.guard_policy_heads h JOIN agent.guard_policies p "
    "ON p.id = h.id AND p.version = h.current_version"
)


class PolicyStore:
    def __init__(self, db: Database, redis: Any = None, *, namespace: str = "agent", refresh_s: float = 30.0):
        self._db = db
        self._redis = redis
        self._cache_key = f"{namespace}:guard:policies"
        self._channel = f"{namespace}:guard:policies:changed"
        self._refresh_s = refresh_s
        self._active: list[Policy] = []
        self._tasks: list[asyncio.Task] = []

    # ------------------------------------------------------------------ what the engine reads

    def active(self) -> Sequence[Policy]:
        return self._active

    async def load(self) -> list[Policy]:
        """Reloads the enabled current versions from Postgres and rewrites the cache."""
        async with self._db.connection() as conn:
            rows = await (await conn.execute(_SELECT_CURRENT + " WHERE h.enabled ORDER BY p.id")).fetchall()
        self._active = [_policy(row) for row in rows]
        await self._write_cache()
        return self._active

    async def _write_cache(self) -> None:
        if self._redis is None:
            return
        document = json.dumps([p.model_dump(mode="json") for p in self._active], ensure_ascii=False)
        try:
            await self._redis.set(self._cache_key, document)
        except Exception as exc:  # Postgres stays the source of truth
            log.warning("policy cache write failed: %s", type(exc).__name__)

    async def _reload_from_cache(self) -> None:
        try:
            raw = await self._redis.get(self._cache_key)
        except Exception as exc:
            log.warning("policy cache read failed, reloading from Postgres: %s", type(exc).__name__)
            raw = None
        if raw is None:
            await self.load()
            return
        self._active = [Policy.model_validate(item) for item in json.loads(raw)]

    async def _changed(self) -> None:
        """After a write: reload from Postgres, rewrite the cache, tell every process."""
        await self.load()
        if self._redis is not None:
            try:
                await self._redis.publish(self._channel, "changed")
            except Exception as exc:
                log.warning("policy change not published: %s", type(exc).__name__)

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
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
                log.warning("policy change listener lost Redis (%s); retrying", type(exc).__name__)
                await asyncio.sleep(1.0)

    async def _refresh(self) -> None:
        while True:
            await asyncio.sleep(self._refresh_s)
            try:
                await self.load()
            except Exception as exc:
                log.warning("periodic policy reload failed: %s", type(exc).__name__)

    # ------------------------------------------------------------------ admin reads

    async def list_policies(self) -> list[PolicyView]:
        async with self._db.connection() as conn:
            rows = await (await conn.execute(_SELECT_CURRENT + " ORDER BY p.id")).fetchall()
        return [_view(r) for r in rows]

    async def get(self, policy_id: str) -> PolicyView:
        async with self._db.connection() as conn:
            row = await (await conn.execute(_SELECT_CURRENT + " WHERE h.id = %s", (policy_id,))).fetchone()
        if row is None:
            raise PolicyNotFound(policy_id)
        return _view(row)

    async def versions(self, policy_id: str) -> list[PolicyVersion]:
        async with self._db.connection() as conn:
            rows = await (
                await conn.execute(
                    "SELECT p.id, p.version, p.content, p.created_at, p.created_by, p.overrides, "
                    "p.exception, h.enabled "
                    "FROM agent.guard_policies p JOIN agent.guard_policy_heads h ON h.id = p.id "
                    "WHERE p.id = %s ORDER BY p.version DESC",
                    (policy_id,),
                )
            ).fetchall()
        if not rows:
            raise PolicyNotFound(policy_id)
        return [
            PolicyVersion(_policy(r), r["created_at"], r["created_by"], list(r["overrides"]), r["exception"])
            for r in rows
        ]

    # ------------------------------------------------------------------ admin writes

    async def create(self, policy: Policy, actor: str) -> Policy:
        async with self._db.connection() as conn, conn.transaction():
            created = await self._insert_new(conn, policy, actor)
            if created is None:
                raise PolicyExists(policy.id)
        await self._changed()
        return created

    async def add_version(self, policy: Policy, actor: str) -> Policy:
        """Inserts `policy`'s content as the next version of its id and makes it current;
        the head's switch follows `policy.enabled`."""
        async with self._db.connection() as conn, conn.transaction():
            created = await self._insert_version(conn, policy, actor)
        await self._changed()
        return created

    async def set_enabled(self, policy_id: str, enabled: bool, actor: str) -> Policy:
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "UPDATE agent.guard_policy_heads SET enabled = %s, updated_at = now(), updated_by = %s "
                "WHERE id = %s",
                (enabled, actor, policy_id),
            )
            if cursor.rowcount != 1:
                raise PolicyNotFound(policy_id)
        await self._changed()
        return (await self.get(policy_id)).policy

    async def import_policies(self, policies: Sequence[Policy], actor: str) -> ImportReport:
        """In one transaction: an absent id is created, a changed one gets a new version,
        one whose switch alone differs is switched, an identical one is left alone."""
        report = ImportReport()
        async with self._db.connection() as conn, conn.transaction():
            for policy in policies:
                row = await (
                    await conn.execute(_SELECT_CURRENT + " WHERE h.id = %s FOR UPDATE OF h", (policy.id,))
                ).fetchone()
                if row is None:
                    await self._insert_new(conn, policy, actor)
                    report.created.append(policy.id)
                    continue
                current = _policy(row)
                if current.content() != policy.content():
                    await self._insert_version(conn, policy, actor)
                    report.versioned.append(policy.id)
                elif current.enabled != policy.enabled:
                    await conn.execute(
                        "UPDATE agent.guard_policy_heads "
                        "SET enabled = %s, updated_at = now(), updated_by = %s WHERE id = %s",
                        (policy.enabled, actor, policy.id),
                    )
                    report.switched.append(policy.id)
                else:
                    report.unchanged.append(policy.id)
        if report.created or report.versioned or report.switched:
            await self._changed()
        return report

    async def seed(self, roots: Iterable[Path]) -> list[str]:
        """Inserts the policies of every `*.yaml` under `roots` whose id is absent (I2)."""
        policies: list[Policy] = []
        for root in roots:
            for path in sorted(root.glob("*.yaml")) if root.is_dir() else []:
                policies += parse_policy_documents(path.read_text(encoding="utf-8"), source=str(path))
        inserted = []
        async with self._db.connection() as conn, conn.transaction():
            for policy in policies:
                if await self._insert_new(conn, policy, SEED_ACTOR) is not None:
                    inserted.append(policy.id)
        if inserted:
            log.info("seeded guard policies: %s", ", ".join(inserted))
            await self._changed()
        return inserted

    async def overrides(self, policy: Policy) -> Overrides:
        """What `policy`, saved now, would override (the admin form's preview)."""
        async with self._db.connection() as conn:
            return await self._overrides(conn, policy)

    async def _overrides(self, conn, policy: Policy) -> Overrides:
        """Against every other policy's current version, on or off (ADR-0105)."""
        rows = await (await conn.execute(_SELECT_CURRENT + " WHERE h.id <> %s", (policy.id,))).fetchall()
        wider = overridden(policy, [_policy(r) for r in rows])
        return Overrides(sorted(p.ref for p in wider), is_exception(policy, wider))

    async def _insert_new(self, conn, policy: Policy, actor: str) -> Policy | None:
        """Version 1 and its head; None when the id exists (concurrent seeders agree)."""
        found = await self._overrides(conn, policy)
        cursor = await conn.execute(
            "INSERT INTO agent.guard_policies (id, version, content, created_by, overrides, exception) "
            "VALUES (%s, 1, %s, %s, %s, %s) ON CONFLICT (id, version) DO NOTHING",
            (policy.id, Jsonb(policy.content()), actor, Jsonb(found.refs), found.exception),
        )
        if cursor.rowcount != 1:
            return None
        await conn.execute(
            "INSERT INTO agent.guard_policy_heads (id, current_version, enabled, updated_by) "
            "VALUES (%s, 1, %s, %s)",
            (policy.id, policy.enabled, actor),
        )
        return policy.model_copy(update={"version": 1})

    async def _insert_version(self, conn, policy: Policy, actor: str) -> Policy:
        row = await (
            await conn.execute(
                "SELECT current_version FROM agent.guard_policy_heads WHERE id = %s FOR UPDATE", (policy.id,)
            )
        ).fetchone()
        if row is None:
            raise PolicyNotFound(policy.id)
        latest = await (
            await conn.execute(
                "SELECT max(version) AS v FROM agent.guard_policies WHERE id = %s", (policy.id,)
            )
        ).fetchone()
        version = latest["v"] + 1
        found = await self._overrides(conn, policy)
        await conn.execute(
            "INSERT INTO agent.guard_policies (id, version, content, created_by, overrides, exception) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (policy.id, version, Jsonb(policy.content()), actor, Jsonb(found.refs), found.exception),
        )
        await conn.execute(
            "UPDATE agent.guard_policy_heads SET current_version = %s, enabled = %s, updated_at = now(), "
            "updated_by = %s WHERE id = %s",
            (version, policy.enabled, actor, policy.id),
        )
        return policy.model_copy(update={"version": version})

    # ------------------------------------------------------------------ matches per day

    async def record_events(
        self,
        events: Sequence[GuardEventRecord],
        *,
        tenant_id: str,
        conversation_id: str | None,
        turn_id: str | None,
    ) -> int:
        """Stores the matched policy evaluations of a turn: no text, ever (D5)."""
        rows = [e for e in events if e.policy_id and e.action != "allow"]
        if not rows:
            return 0
        async with self._db.connection() as conn, conn.cursor() as cursor:
            await cursor.executemany(
                "INSERT INTO agent.guard_events (tenant_id, conversation_id, turn_id, checkpoint, "
                "policy_id, policy_version, action, target) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        tenant_id,
                        conversation_id or None,
                        turn_id,
                        e.checkpoint.value,
                        e.policy_id,
                        e.policy_version,
                        e.action,
                        e.target,
                    )
                    for e in rows
                ],
            )
        return len(rows)

    async def matches_per_day(self, days: int) -> list[dict[str, Any]]:
        async with self._db.connection() as conn:
            rows = await (
                await conn.execute(
                    "SELECT (created_at AT TIME ZONE 'UTC')::date AS day, policy_id, action, "
                    "count(*) AS matches FROM agent.guard_events "
                    "WHERE created_at >= now() - make_interval(days => %s) "
                    "GROUP BY 1, 2, 3 ORDER BY 1, 2, 3",
                    (days,),
                )
            ).fetchall()
        return [
            {
                "day": r["day"].isoformat(),
                "policy_id": r["policy_id"],
                "action": r["action"],
                "matches": r["matches"],
            }
            for r in rows
        ]
