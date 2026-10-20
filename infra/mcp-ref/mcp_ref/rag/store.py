"""The reference RAG server's document records (rag_ref.documents). Every read names the scope's
tenant and, for a session collection, its user: the server enforces the scope itself (I10, G6)."""

import json
import uuid
from dataclasses import dataclass
from typing import Any

from app.db import Database
from app.rag.collections import kind_of
from app.rag.types import Owner

_COLUMNS = (
    "id::text AS id, tenant_id, user_id, collection, name, mime, kind, size, object_key, status, reason, "
    "pages, warnings, attempts"
)


def owner_of(tenant_id: str, user_id: str, collection: str) -> Owner:
    """A session collection's documents are one user's; a knowledge base's are its tenant's."""
    return Owner(tenant_id=tenant_id, user_id=user_id if kind_of(collection) == "session" else "")


@dataclass(frozen=True)
class Record:
    id: str
    tenant_id: str
    user_id: str
    collection: str
    name: str
    mime: str
    kind: str
    size: int
    object_key: str
    status: str
    reason: str | None
    pages: int | None
    warnings: list[dict[str, Any]]
    attempts: int

    @property
    def owner(self) -> Owner:
        return Owner(tenant_id=self.tenant_id, user_id=self.user_id)


def _record(row) -> Record:
    data = dict(row)
    if isinstance(data["warnings"], str):
        data["warnings"] = json.loads(data["warnings"])
    return Record(**data)


def _valid_id(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


class Documents:
    def __init__(self, db: Database):
        self._db = db

    async def register(self, record: dict[str, Any]) -> None:
        """Idempotent by document id: registering again changes nothing."""
        async with self._db.connection() as conn:
            await conn.execute(
                "INSERT INTO rag_ref.documents (id, tenant_id, user_id, collection, name, mime, kind, size, "
                "object_key) VALUES (%(id)s, %(tenant_id)s, %(user_id)s, %(collection)s, %(name)s, %(mime)s, "
                "%(kind)s, %(size)s, %(object_key)s) ON CONFLICT (id) DO NOTHING",
                record,
            )

    async def get(
        self, document_id: str, tenant_id: str, user_id: str, collections: list[str]
    ) -> Record | None:
        if not _valid_id(document_id):
            return None
        async with self._db.connection() as conn:
            row = await (
                await conn.execute(
                    f"SELECT {_COLUMNS} FROM rag_ref.documents WHERE id = %s AND tenant_id = %s "
                    "AND collection = ANY(%s)",
                    (document_id, tenant_id, collections),
                )
            ).fetchone()
        if row is None:
            return None
        record = _record(row)
        return record if record.user_id == owner_of(tenant_id, user_id, record.collection).user_id else None

    async def indexed_in(self, owner: Owner, collection: str) -> dict[str, str]:
        """The indexed documents of one collection for its owner: id → file name."""
        async with self._db.connection() as conn:
            rows = await (
                await conn.execute(
                    "SELECT id::text AS id, name FROM rag_ref.documents "
                    "WHERE tenant_id = %s AND user_id = %s "
                    "AND collection = %s AND status = 'indexed'",
                    (owner.tenant_id, owner.user_id, collection),
                )
            ).fetchall()
        return {row["id"]: row["name"] for row in rows}

    # ------------------------------------------------------------------ the indexer only

    async def by_id(self, document_id: str) -> Record | None:
        async with self._db.connection() as conn:
            row = await (
                await conn.execute(f"SELECT {_COLUMNS} FROM rag_ref.documents WHERE id = %s", (document_id,))
            ).fetchone()
        return _record(row) if row else None

    async def start_attempt(self, document_id: str) -> Record | None:
        async with self._db.connection() as conn:
            row = await (
                await conn.execute(
                    "UPDATE rag_ref.documents SET attempts = attempts + 1 "
                    "WHERE id = %s AND status = 'pending' "
                    f"RETURNING {_COLUMNS}",
                    (document_id,),
                )
            ).fetchone()
        return _record(row) if row else None

    async def mark_indexed(self, document_id: str, *, pages: int | None, warnings: list[dict], conn) -> None:
        await conn.execute(
            "UPDATE rag_ref.documents SET status = 'indexed', reason = NULL, pages = %s, "
            "warnings = %s::jsonb, "
            "indexed_at = now() WHERE id = %s",
            (pages, json.dumps(warnings), document_id),
        )

    async def mark_failed(self, document_id: str, reason: str, *, pages: int | None = None) -> None:
        async with self._db.connection() as conn:
            await conn.execute(
                "UPDATE rag_ref.documents SET status = 'failed', reason = %s, pages = %s WHERE id = %s "
                "AND status = 'pending'",
                (reason, pages, document_id),
            )

    async def pending_ids(self) -> list[str]:
        async with self._db.connection() as conn:
            rows = await (
                await conn.execute("SELECT id::text AS id FROM rag_ref.documents WHERE status = 'pending'")
            ).fetchall()
        return [row["id"] for row in rows]
