"""Attachment records in Postgres (phase 04, A1–A3; ADR-0129, ADR-0130).

The gateway stores an upload's bytes in MinIO and registers it here; the agent owns the
record. Every method a request can reach names the owner (tenant, user) and the
conversation: no request reads or lists another user's attachment, whatever id it sends
(G6, the memory precedent of ADR-0059). Nothing here deletes a record: the product deletes no
file (owner decision S4); operations do, and the database's keys take the chunks with the
record. The ingest service and the orphan sweeper use the methods below the line, never
reachable from a request."""

import uuid
from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb
from pydantic import BaseModel

from app.db import Database
from app.rag.collections import session_collection
from app.rag.types import AttachmentStatus, DocumentKind, FailureReason, Owner


def collection_for(conversation_id: str) -> str:
    """A conversation's session-scoped collection (README §9.3, ADR-0131)."""
    return session_collection(conversation_id)


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
    backend: str = "internal"  # the DocumentBackend it was registered with (I11)
    status: AttachmentStatus
    reason: FailureReason | None = None
    pages: int | None = None
    chunks: int | None = None
    # D7 (ADR-0201): [{code: pages_without_text, count}] on an indexed PDF with pages not read.
    warnings: list[dict[str, Any]] = []
    attempts: int = 0
    created_at: datetime
    updated_at: datetime
    indexed_at: datetime | None = None

    @property
    def owner(self) -> Owner:
        return Owner(tenant_id=self.tenant_id, user_id=self.user_id)


class LimitReached(Exception):
    """The conversation already holds the configured number of files (ADR-0126)."""


_COLUMNS = (
    "id::text AS id, conversation_id::text AS conversation_id, tenant_id, user_id, name, mime, kind, "
    "size, object_key, collection, backend, status, reason, pages, chunks, warnings, attempts, created_at, "
    "updated_at, indexed_at"
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
        backend: str = "internal",
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
                "object_key, collection, backend) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
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
                    backend,
                ),
            )
            return _record(await cursor.fetchone())

    async def list_for(self, owner: Owner, conversation_id: str) -> list[AttachmentRecord]:
        """The owner's attachments of one conversation, oldest first."""
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                f"SELECT {_COLUMNS} FROM agent.attachments "
                "WHERE tenant_id = %s AND user_id = %s AND conversation_id = %s "
                "ORDER BY created_at, id",
                (owner.tenant_id, owner.user_id, conversation_id),
            )
            return [_record(row) for row in await cursor.fetchall()]

    async def get(self, owner: Owner, conversation_id: str, attachment_id: str) -> AttachmentRecord | None:
        """None for an id the owner does not have in this conversation, another user's included."""
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                f"SELECT {_COLUMNS} FROM agent.attachments "
                "WHERE id = %s AND tenant_id = %s AND user_id = %s AND conversation_id = %s",
                (attachment_id, owner.tenant_id, owner.user_id, conversation_id),
            )
            row = await cursor.fetchone()
        return _record(row) if row else None

    async def remember_status(
        self,
        owner: Owner,
        attachment_id: str,
        *,
        status: AttachmentStatus,
        reason: FailureReason | None,
        pages: int | None,
        warnings: list[dict[str, Any]],
    ) -> None:
        """Keeps the status another backend last reported on the owner's record, so a read that
        cannot reach that backend shows it (ADR-0233). The internal backend's records are its
        status already and are never written here."""
        async with self._db.connection() as conn:
            await conn.execute(
                "UPDATE agent.attachments SET status = %s, reason = %s, pages = %s, warnings = %s, "
                "indexed_at = CASE WHEN %s = 'indexed' THEN coalesce(indexed_at, now()) ELSE indexed_at END, "
                "updated_at = now() "
                "WHERE id = %s AND tenant_id = %s AND user_id = %s AND backend <> 'internal' "
                # A failed registration is final (ADR-0254): what the backend says later never undoes it.
                "AND reason IS DISTINCT FROM 'register_failed' "
                "AND (status, coalesce(reason, ''), coalesce(pages, -1), warnings) "
                "IS DISTINCT FROM (%s, coalesce(%s, ''), coalesce(%s, -1), %s)",
                (
                    status,
                    reason,
                    pages,
                    Jsonb(warnings),
                    status,
                    attachment_id,
                    owner.tenant_id,
                    owner.user_id,
                    status,
                    reason,
                    pages,
                    Jsonb(warnings),
                ),
            )

    # ------------------------------------------------------------------ ingest service and sweeper only

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

    async def mark_indexed(
        self,
        attachment_id: str,
        *,
        pages: int | None,
        chunks: int,
        conn,
        warnings: list[dict[str, Any]] | None = None,
    ) -> bool:
        cursor = await conn.execute(
            "UPDATE agent.attachments SET status = 'indexed', reason = NULL, pages = %s, chunks = %s, "
            "warnings = %s, indexed_at = now(), updated_at = now() WHERE id = %s AND status = 'pending'",
            (pages, chunks, Jsonb(warnings or []), attachment_id),
        )
        return cursor.rowcount == 1

    async def mark_register_failed(self, attachment_id: str) -> None:
        """The backend did not take the file (ADR-0254). Final, also over a status another backend
        reported meanwhile (it may have indexed the file and then failed the call)."""
        async with self._db.connection() as conn:
            await conn.execute(
                "UPDATE agent.attachments SET status = 'failed', reason = 'register_failed', "
                "updated_at = now() WHERE id = %s AND (status = 'pending' OR backend <> 'internal')",
                (attachment_id,),
            )

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
        """The internal backend's pending uploads; another backend's are its own to index (I11)."""
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT id::text AS id FROM agent.attachments "
                "WHERE status = 'pending' AND backend = 'internal' "
                "ORDER BY created_at"
            )
            return [row["id"] for row in await cursor.fetchall()]

    async def known_keys(self, keys: list[str]) -> set[str]:
        """The object keys among `keys` that an attachment record names (the orphan sweeper)."""
        if not keys:
            return set()
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT object_key FROM agent.attachments WHERE object_key = ANY(%s)", (keys,)
            )
            return {row["object_key"] for row in await cursor.fetchall()}
