"""Attachment records in Postgres (phase 04, A1–A4; ADR-0125, ADR-0129, ADR-0130).

The gateway stores an upload's bytes in MinIO and registers it here; the agent owns the
record. Every method a request can reach names the owner (tenant, user) and the
conversation: no request reads, lists or deletes another user's attachment, whatever
id it sends (G6, the memory precedent of ADR-0059). The worker and the purge use the
by-id methods below the line, never reachable from a request."""

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.db import Database
from app.rag.types import AttachmentStatus, DocumentKind, FailureReason, Owner


def collection_for(conversation_id: str) -> str:
    """A conversation's session-scoped collection (README §9.3, ADR-0131)."""
    return f"sess_{conversation_id}"


def conversation_lock_key(conversation_id: str) -> int:
    """A transaction-scoped advisory lock key for counting a conversation's files. Its low
    64 bits, so it never equals the turn lock's key (upper 64 bits, app/api/locks.py)."""
    value = uuid.UUID(conversation_id).int & ((1 << 64) - 1)
    return value - (1 << 64) if value >= 1 << 63 else value


class AttachmentRecord(BaseModel):
    id: str
    conversation_id: str
    tenant_id: str
    user_id: str
    name: str
    mime: str
    kind: DocumentKind
    size: int
    object_key: str
    collection: str
    status: AttachmentStatus
    reason: FailureReason | None = None
    pages: int | None = None
    chunks: int | None = None
    attempts: int = 0
    created_at: datetime
    updated_at: datetime
    indexed_at: datetime | None = None
    expires_at: datetime

    @property
    def owner(self) -> Owner:
        return Owner(tenant_id=self.tenant_id, user_id=self.user_id)


class LimitReached(Exception):
    """The conversation already holds the configured number of files (ADR-0126)."""


_COLUMNS = (
    "id::text AS id, conversation_id::text AS conversation_id, tenant_id, user_id, name, mime, kind, "
    "size, object_key, collection, status, reason, pages, chunks, attempts, created_at, updated_at, "
    "indexed_at, expires_at"
)


def _record(row) -> AttachmentRecord:
    return AttachmentRecord.model_validate(dict(row))


class AttachmentStore:
    def __init__(self, db: Database):
        self._db = db

    # ------------------------------------------------------------------ owner-scoped

    async def register(
        self,
        owner: Owner,
        conversation_id: str,
        *,
        name: str,
        mime: str,
        kind: DocumentKind,
        size: int,
        object_key: str,
        max_files: int,
        retention_days: int,
    ) -> AttachmentRecord:
        """Inserts a pending attachment, unless the conversation already holds `max_files`;
        the count and the insert run under the conversation's transaction lock, so two
        concurrent uploads cannot both take the last place."""
        async with self._db.connection() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(%s)", (conversation_lock_key(conversation_id),))
            cursor = await conn.execute(
                "SELECT count(*) AS n FROM agent.attachments "
                "WHERE tenant_id = %s AND user_id = %s AND conversation_id = %s",
                (owner.tenant_id, owner.user_id, conversation_id),
            )
            if (await cursor.fetchone())["n"] >= max_files:
                raise LimitReached(conversation_id)
            cursor = await conn.execute(
                "INSERT INTO agent.attachments (conversation_id, tenant_id, user_id, name, mime, kind, size, "
                "object_key, collection, expires_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now() + make_interval(days => %s)) "
                f"RETURNING {_COLUMNS}",
                (
                    conversation_id,
                    owner.tenant_id,
                    owner.user_id,
                    name,
                    mime,
                    kind,
                    size,
                    object_key,
                    collection_for(conversation_id),
                    retention_days,
                ),
            )
            return _record(await cursor.fetchone())

    async def list_for(self, owner: Owner, conversation_id: str) -> list[AttachmentRecord]:
        """The owner's attachments of one conversation that have not expired, oldest first."""
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                f"SELECT {_COLUMNS} FROM agent.attachments "
                "WHERE tenant_id = %s AND user_id = %s AND conversation_id = %s AND expires_at > now() "
                "ORDER BY created_at, id",
                (owner.tenant_id, owner.user_id, conversation_id),
            )
            return [_record(row) for row in await cursor.fetchall()]

    async def get(self, owner: Owner, conversation_id: str, attachment_id: str) -> AttachmentRecord | None:
        """None for an id the owner does not have in this conversation, another user's included."""
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                f"SELECT {_COLUMNS} FROM agent.attachments "
                "WHERE id = %s AND tenant_id = %s AND user_id = %s AND conversation_id = %s "
                "AND expires_at > now()",
                (attachment_id, owner.tenant_id, owner.user_id, conversation_id),
            )
            row = await cursor.fetchone()
        return _record(row) if row else None

    async def delete(self, owner: Owner, conversation_id: str, attachment_id: str, *, conn) -> bool:
        """Deletes the owner's record on the caller's connection (the service deletes its
        chunks in the same transaction)."""
        cursor = await conn.execute(
            "DELETE FROM agent.attachments WHERE id = %s AND tenant_id = %s AND user_id = %s "
            "AND conversation_id = %s",
            (attachment_id, owner.tenant_id, owner.user_id, conversation_id),
        )
        return cursor.rowcount == 1

    async def touch(self, owner: Owner, conversation_id: str, expires_at: datetime, *, conn=None) -> int:
        """A turn of the conversation: its live attachments now expire at `expires_at` (D1)."""
        sql = (
            "UPDATE agent.attachments SET expires_at = %s "
            "WHERE tenant_id = %s AND user_id = %s AND conversation_id = %s AND expires_at > now()"
        )
        params = (expires_at, owner.tenant_id, owner.user_id, conversation_id)
        if conn is not None:
            return (await conn.execute(sql, params)).rowcount
        async with self._db.connection() as own:
            return (await own.execute(sql, params)).rowcount

    # ------------------------------------------------------------------ worker and purge only

    async def by_id(self, attachment_id: str) -> AttachmentRecord | None:
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                f"SELECT {_COLUMNS} FROM agent.attachments WHERE id = %s", (attachment_id,)
            )
            row = await cursor.fetchone()
        return _record(row) if row else None

    async def start_attempt(self, attachment_id: str) -> AttachmentRecord | None:
        """Counts an ingestion attempt of a pending attachment; None when it is gone or done."""
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "UPDATE agent.attachments SET attempts = attempts + 1, updated_at = now() "
                f"WHERE id = %s AND status = 'pending' RETURNING {_COLUMNS}",
                (attachment_id,),
            )
            row = await cursor.fetchone()
        return _record(row) if row else None

    async def mark_indexed(self, attachment_id: str, *, pages: int | None, chunks: int, conn) -> bool:
        cursor = await conn.execute(
            "UPDATE agent.attachments SET status = 'indexed', reason = NULL, pages = %s, chunks = %s, "
            "indexed_at = now(), updated_at = now() WHERE id = %s AND status = 'pending'",
            (pages, chunks, attachment_id),
        )
        return cursor.rowcount == 1

    async def mark_failed(
        self, attachment_id: str, reason: FailureReason, *, pages: int | None = None
    ) -> bool:
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "UPDATE agent.attachments SET status = 'failed', reason = %s, pages = %s, updated_at = now() "
                "WHERE id = %s AND status = 'pending'",
                (reason, pages, attachment_id),
            )
            return cursor.rowcount == 1

    async def pending_ids(self) -> list[str]:
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT id::text AS id FROM agent.attachments WHERE status = 'pending' ORDER BY created_at"
            )
            return [row["id"] for row in await cursor.fetchall()]

    async def expired(self, limit: int = 500) -> list[AttachmentRecord]:
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                f"SELECT {_COLUMNS} FROM agent.attachments WHERE expires_at <= now() "
                "ORDER BY expires_at LIMIT %s",
                (limit,),
            )
            return [_record(row) for row in await cursor.fetchall()]

    async def delete_by_id(self, attachment_id: str, *, conn) -> bool:
        cursor = await conn.execute("DELETE FROM agent.attachments WHERE id = %s", (attachment_id,))
        return cursor.rowcount == 1
