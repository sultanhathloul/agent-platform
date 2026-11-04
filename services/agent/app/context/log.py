"""Recorded context packages behind GET /v1/debug/context/{turn_id} (README §7,
ADR-0059). Packages carry user content, so nothing is written unless
DEBUG_CONTEXT_ENABLED is true.

Retention (D5): a package expires DEBUG_CONTEXT_RETENTION_DAYS after it is recorded
(rows recorded before expiry existed count from their creation) and is never served
after that; expired rows are deleted as new ones are written. Each row names the
memory items its memory block used, so deleting a memory item deletes every
package that holds its text (app/memory/store.py), and the attachments whose chunks its
retrieval blocks hold, so operations deleting a file find every package that quotes it
(owner decision S4).

A package also names every file the turn could have put into it (my-files review, ADR-0448): the
conversation's attachments, whose names reach the router, INPUT and the docs findings, and the documents
the capabilities' sources name, whose names and passages reach the judge, the findings and the answer. A
package recorded before the turn knew a file is tagged with it afterwards (`tag_turn`). A delete may so
remove a package that held none of the file, never one that held it. A package naming a deleted file
(agent.deleted_attachments) is not recorded."""

import re
import time
from collections.abc import Collection
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


_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def file_ids(ids: Collection[Any]) -> list[str]:
    """The document ids among `ids` (uuids), lower-cased, once each."""
    return list(dict.fromkeys(i.lower() for i in ids if isinstance(i, str) and _UUID.match(i)))


def attachment_ids(package: ContextPackage, files: Collection[str] = ()) -> list[str]:
    """The attachments whose chunks the package's retrieval blocks hold and `files`, the turn's files
    (ADR-0448), once each."""
    ids = [b.meta.get("document_id") for b in package.blocks if b.name == "retrieval"]
    return file_ids([*ids, *files])


# A package or a tag naming a file the user deleted is not written (ADR-0448).
_NOT_DELETED = "NOT EXISTS (SELECT 1 FROM agent.deleted_attachments d WHERE d.id = ANY(%s::uuid[]))"


class ContextLog:
    def __init__(self, db: Database, *, enabled: bool, retention_days: int = 7):
        self._db = db
        self.enabled = enabled
        self.retention_days = retention_days
        self._purged_at = 0.0

    async def record(
        self, *, turn_id: str, conversation_id: str, package: ContextPackage, files: Collection[str] = ()
    ) -> bool:
        """The exact package a model call received, in assembly order, naming `files`, the turn's files
        (ADR-0448). False, and nothing written, when it names a deleted file."""
        if not self.enabled or not conversation_id:
            return False
        document = {**package.model_dump(mode="json"), "accounting": package.accounting()}
        ids = attachment_ids(package, files)
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "INSERT INTO agent.context_packages "
                "(turn_id, conversation_id, node, package, expires_at, memory_item_ids, attachment_ids) "
                "SELECT %s, %s, %s, %s, now() + make_interval(days => %s), %s::uuid[], %s::uuid[] "
                f"WHERE {_NOT_DELETED}",
                (
                    turn_id,
                    conversation_id,
                    package.node,
                    Jsonb(document),
                    self.retention_days,
                    memory_item_ids(package),
                    ids,
                    ids,
                ),
            )
            written = cursor.rowcount == 1
        if time.monotonic() - self._purged_at > _PURGE_EVERY_S:
            await self.purge_expired()
        return written

    async def tag_turn(self, turn_id: str, files: Collection[str]) -> None:
        """Names `files` on every package the turn recorded so far (ADR-0448), and removes the turn's packages
        once any of them is a deleted file."""
        ids = file_ids(files)
        if not self.enabled or not ids:
            return
        async with self._db.connection() as conn:
            await conn.execute(
                "UPDATE agent.context_packages SET attachment_ids = ARRAY("
                "SELECT DISTINCT u FROM unnest(attachment_ids || %s::uuid[]) AS u) WHERE turn_id = %s",
                (ids, turn_id),
            )
            await conn.execute(
                f"DELETE FROM agent.context_packages WHERE turn_id = %s AND NOT ({_NOT_DELETED})",
                (turn_id, ids),
            )

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
