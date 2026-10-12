"""Recorded context packages behind GET /v1/debug/context/{turn_id} (README §7,
ADR-0059). Packages carry user content, so nothing is written unless
DEBUG_CONTEXT_ENABLED is true.

Retention (D5): a package expires DEBUG_CONTEXT_RETENTION_DAYS after it is recorded
(rows recorded before expiry existed count from their creation) and is never served
after that; expired rows are deleted as new ones are written. Each row names the
memory items its memory block used, so deleting a memory item deletes every
package that holds its text (app/memory/store.py), and the attachments whose chunks its
retrieval blocks hold, so operations deleting a file find every package that quotes it
(owner decision S4)."""

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb

from app.context.package import ContextPackage
from app.db import Database


@dataclass(frozen=True)
class RecordedPackage:
    node: str
    created_at: datetime
    package: dict[str, Any]


@dataclass(frozen=True)
class RecordedTurn:
    conversation_id: str
    packages: list[RecordedPackage]


# A row with no expiry (recorded before D5) expires `retention_days` after its creation.
_LIVE = "COALESCE(expires_at, created_at + make_interval(days => %(days)s)) > now()"
_PURGE_EVERY_S = 60.0


def memory_item_ids(package: ContextPackage) -> list[str]:
    """The ids of the memory items in the package's memory block."""
    return [item["id"] for b in package.blocks if b.name == "memory" for item in b.meta.get("items", [])]


def attachment_ids(package: ContextPackage) -> list[str]:
    """The attachments whose chunks the package's retrieval blocks hold, once each."""
    ids = [b.meta.get("document_id") for b in package.blocks if b.name == "retrieval"]
    return list(dict.fromkeys(i for i in ids if i))


class ContextLog:
    def __init__(self, db: Database, *, enabled: bool, retention_days: int = 7):
        self._db = db
        self.enabled = enabled
        self.retention_days = retention_days
        self._purged_at = 0.0

    async def record(self, *, turn_id: str, conversation_id: str, package: ContextPackage) -> None:
        """The exact package a model call received, in assembly order."""
        if not self.enabled or not conversation_id:
            return
        document = {**package.model_dump(mode="json"), "accounting": package.accounting()}
        async with self._db.connection() as conn:
            await conn.execute(
                "INSERT INTO agent.context_packages "
                "(turn_id, conversation_id, node, package, expires_at, memory_item_ids, attachment_ids) "
                "VALUES (%s, %s, %s, %s, now() + make_interval(days => %s), %s::uuid[], %s::uuid[])",
                (
                    turn_id,
                    conversation_id,
                    package.node,
                    Jsonb(document),
                    self.retention_days,
                    memory_item_ids(package),
                    attachment_ids(package),
                ),
            )
        if time.monotonic() - self._purged_at > _PURGE_EVERY_S:
            await self.purge_expired()

    async def purge_expired(self) -> int:
        self._purged_at = time.monotonic()
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                f"DELETE FROM agent.context_packages WHERE NOT ({_LIVE})", {"days": self.retention_days}
            )
            return cursor.rowcount

    async def turn(self, turn_id: str) -> RecordedTurn | None:
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT conversation_id, node, package, created_at FROM agent.context_packages "
                f"WHERE turn_id = %(turn_id)s AND {_LIVE} ORDER BY id",
                {"turn_id": turn_id, "days": self.retention_days},
            )
            rows = await cursor.fetchall()
        if not rows:
            return None
        return RecordedTurn(
            conversation_id=str(rows[0]["conversation_id"]),
            packages=[RecordedPackage(r["node"], r["created_at"], r["package"]) for r in rows],
        )
