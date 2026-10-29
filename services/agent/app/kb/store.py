"""Knowledge bases and their documents in Postgres (phase 08, ADR-0333 to ADR-0339).

A knowledge base belongs to the tenant of the administrator who created it (ADR-0202: a knowledge
base collection is its tenant's); its slug is unique on the platform, because its realm role
`kb:<slug>:read` is. Every method a request can reach names the tenant: no request reads another
tenant's KB, whatever slug it sends. Deleting a KB is soft (I1): its row stays, hidden, and its
slug stays taken. A KB document carries the source metadata of D3; `supersedes` names an earlier
version of the same source in the same KB, at most one document supersedes another, and no chain
loops (checked under the KB's transaction lock). Nothing here deletes a row: the product deletes no
file (ADR-0166). The ingest service and the orphan sweeper use the methods below the line."""

import re
from datetime import date, datetime
from typing import Any

from psycopg.types.json import Jsonb
from pydantic import BaseModel

from app.db import Database
from app.rag.collections import kb_collection
from app.rag.types import AttachmentStatus, DocumentKind, FailureReason, Owner

SLUG_PATTERN = r"^[a-z][a-z0-9_-]{0,62}$"
_SLUG = re.compile(SLUG_PATTERN)


def read_permission(slug: str) -> str:
    """The realm role that reads a KB (ADR-0006)."""
    return f"kb:{slug}:read"


def kb_owner(tenant_id: str) -> Owner:
    """Whose a KB's chunks are: its tenant's, with no user (the reference RAG server's rule too)."""
    return Owner(tenant_id=tenant_id, user_id="")


class Actor(BaseModel):
    """Who acted, as the gateway named them from the verified token (ADR-0123)."""

    user_id: str
    username: str | None = None
    name: str | None = None


class KbRecord(BaseModel):
    slug: str
    tenant_id: str
    name_ar: str
    name_en: str
    description_ar: str = ""
    description_en: str = ""
    icon: str = "library"
    created_at: datetime
    created_by: str
    created_by_username: str | None = None
    created_by_name: str | None = None
    deleted_at: datetime | None = None
    deleted_by: str | None = None
    deleted_by_username: str | None = None
    deleted_by_name: str | None = None
    index_purged_at: datetime | None = None

    @property
    def role(self) -> str:
        return read_permission(self.slug)

    @property
    def collection(self) -> str:
        return kb_collection(self.slug)


class SourceMetadata(BaseModel):
    """D3: what an administrator says about a document's source."""

    title: str
    publisher: str
    source_url: str | None = None
    published_on: date
    supersedes: str | None = None


class KbDocumentRecord(BaseModel):
    id: str
    kb_slug: str
    tenant_id: str
    name: str
    mime: str
    kind: DocumentKind
    size: int
    object_key: str
    collection: str
    backend: str = "internal"
    status: AttachmentStatus
    reason: FailureReason | None = None
    pages: int | None = None
    chunks: int | None = None
    warnings: list[dict[str, Any]] = []
    attempts: int = 0
    title: str
    publisher: str
    source_url: str | None = None
    published_on: date
    supersedes: str | None = None
    uploaded_by: str
    uploaded_by_username: str | None = None
    uploaded_by_name: str | None = None
    created_at: datetime
    updated_at: datetime
    indexed_at: datetime | None = None
    purged_at: datetime | None = None
    # The file's SHA-256 as the gateway stored it, and whether its source metadata was missing at upload
    # (ADR-0392); None for a document registered before migration 0018.
    sha256: str | None = None
    metadata_missing: bool = False

    @property
    def owner(self) -> Owner:
        return kb_owner(self.tenant_id)

    @property
    def metadata(self) -> SourceMetadata:
        return SourceMetadata(
            title=self.title,
            publisher=self.publisher,
            source_url=self.source_url,
            published_on=self.published_on,
            supersedes=self.supersedes,
        )


class KbExists(Exception):
    """The slug is taken, by a live KB or a deleted one (its role stays, I1)."""


class InvalidSupersedes(Exception):
    """`supersedes` names no document of this KB, the document itself, one another document already
    supersedes, or would close a loop."""


_KB_COLUMNS = (
    "slug, tenant_id, name_ar, name_en, description_ar, description_en, icon, created_at, created_by, "
    "created_by_username, created_by_name, deleted_at, deleted_by, deleted_by_username, deleted_by_name, "
    "index_purged_at"
)
_DOC_COLUMNS = (
    "id::text AS id, kb_slug, tenant_id, name, mime, kind, size, object_key, collection, backend, status, "
    "reason, pages, chunks, warnings, attempts, title, publisher, source_url, published_on, "
    "supersedes::text AS supersedes, uploaded_by, uploaded_by_username, uploaded_by_name, created_at, "
    "updated_at, indexed_at, purged_at, sha256, metadata_missing"
)


# A KB's transaction lock for the supersedes checks: the two-key advisory lock form, whose key space
# never overlaps the one-key form the turn and conversation locks use (PostgreSQL's documented rule).
_KB_LOCK = "SELECT pg_advisory_xact_lock(8, hashtext(%s))"


def _kb(row) -> KbRecord:
    return KbRecord.model_validate(dict(row))


def _doc(row) -> KbDocumentRecord:
    return KbDocumentRecord.model_validate(dict(row))


class KbStore:
    def __init__(self, db: Database):
        self._db = db

    # ------------------------------------------------------------------ knowledge bases

    async def create(
        self,
        tenant_id: str,
        slug: str,
        *,
        name_ar: str,
        name_en: str,
        description_ar: str,
        description_en: str,
        icon: str,
        actor: Actor,
    ) -> KbRecord:
        if not _SLUG.match(slug):
            raise ValueError(f"invalid slug {slug!r}")
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "INSERT INTO agent.kbs (slug, tenant_id, name_ar, name_en, description_ar, description_en, icon, "
                "created_by, created_by_username, created_by_name) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (slug) DO NOTHING "
                f"RETURNING {_KB_COLUMNS}",
                (
                    slug,
                    tenant_id,
                    name_ar,
                    name_en,
                    description_ar,
                    description_en,
                    icon,
                    actor.user_id,
                    actor.username,
                    actor.name,
                ),
            )
            row = await cursor.fetchone()
        if row is None:
            raise KbExists(slug)
        return _kb(row)

    async def list_active(self, tenant_id: str) -> list[KbRecord]:
        """The tenant's live KBs, by slug."""
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                f"SELECT {_KB_COLUMNS} FROM agent.kbs WHERE tenant_id = %s AND deleted_at IS NULL ORDER BY slug",
                (tenant_id,),
            )
            return [_kb(row) for row in await cursor.fetchall()]

    async def get(self, tenant_id: str, slug: str) -> KbRecord | None:
        """A live KB of the tenant; None for another tenant's, a deleted one, or none."""
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                f"SELECT {_KB_COLUMNS} FROM agent.kbs WHERE slug = %s AND tenant_id = %s AND deleted_at IS NULL",
                (slug, tenant_id),
            )
            row = await cursor.fetchone()
        return _kb(row) if row else None

    async def active_slugs(self, tenant_id: str, slugs) -> set[str]:
        """Those of `slugs` that are live KBs of the tenant."""
        wanted = [s for s in dict.fromkeys(slugs) if isinstance(s, str) and _SLUG.match(s)]
        if not wanted:
            return set()
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT slug FROM agent.kbs WHERE tenant_id = %s AND deleted_at IS NULL AND slug = ANY(%s)",
                (tenant_id, wanted),
            )
            return {row["slug"] for row in await cursor.fetchall()}

    async def soft_delete(self, tenant_id: str, slug: str, actor: Actor) -> KbRecord | None:
        """Hides a live KB of the tenant and marks its documents purged; None when there is none.
        The caller purges the index (I1)."""
        async with self._db.connection() as conn, conn.transaction():
            cursor = await conn.execute(
                "UPDATE agent.kbs SET deleted_at = now(), deleted_by = %s, deleted_by_username = %s, "
                "deleted_by_name = %s WHERE slug = %s AND tenant_id = %s AND deleted_at IS NULL "
                f"RETURNING {_KB_COLUMNS}",
                (actor.user_id, actor.username, actor.name, slug, tenant_id),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            await conn.execute(
                "UPDATE agent.kb_documents SET purged_at = now(), updated_at = now() "
                "WHERE kb_slug = %s AND purged_at IS NULL",
                (slug,),
            )
        return _kb(row)

    async def counts(self, tenant_id: str) -> dict[str, tuple[int, int]]:
        """Per live KB of the tenant: its documents and its indexed documents."""
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT d.kb_slug AS slug, count(*) AS total, count(*) FILTER (WHERE d.status = 'indexed') AS indexed "
                "FROM agent.kb_documents d JOIN agent.kbs k ON k.slug = d.kb_slug "
                "WHERE k.tenant_id = %s AND k.deleted_at IS NULL AND d.purged_at IS NULL GROUP BY d.kb_slug",
                (tenant_id,),
            )
            return {row["slug"]: (int(row["total"]), int(row["indexed"])) for row in await cursor.fetchall()}

    async def owed_purges(self) -> list[KbRecord]:
        """Deleted KBs whose chunks have not left the index yet (I1)."""
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                f"SELECT {_KB_COLUMNS} FROM agent.kbs WHERE deleted_at IS NOT NULL AND index_purged_at IS NULL "
                "ORDER BY deleted_at"
            )
            return [_kb(row) for row in await cursor.fetchall()]

    async def mark_index_purged(self, slug: str) -> None:
        async with self._db.connection() as conn:
            await conn.execute(
                "UPDATE agent.kbs SET index_purged_at = now() WHERE slug = %s AND deleted_at IS NOT NULL",
                (slug,),
            )

    async def backends_of(self, slug: str) -> set[str]:
        """The backends a KB's documents were registered with, purged ones included."""
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT DISTINCT backend FROM agent.kb_documents WHERE kb_slug = %s", (slug,)
            )
            return {row["backend"] for row in await cursor.fetchall()}

    # ------------------------------------------------------------------ documents (admin)

    async def register_document(
        self,
        kb: KbRecord,
        *,
        name: str,
        mime: str,
        kind: DocumentKind,
        size: int,
        object_key: str,
        metadata: SourceMetadata,
        backend: str,
        actor: Actor,
        sha256: str | None = None,
        metadata_missing: bool = False,
    ) -> KbDocumentRecord:
        """A pending document of a live KB, with its D3 metadata; raises InvalidSupersedes."""
        async with self._db.connection() as conn, conn.transaction():
            await conn.execute(_KB_LOCK, (kb.slug,))
            await self._check_supersedes(conn, kb.slug, None, metadata.supersedes)
            cursor = await conn.execute(
                "INSERT INTO agent.kb_documents (kb_slug, tenant_id, name, mime, kind, size, object_key, collection, "
                "backend, title, publisher, source_url, published_on, supersedes, uploaded_by, uploaded_by_username, "
                "uploaded_by_name, sha256, metadata_missing) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                f"RETURNING {_DOC_COLUMNS}",
                (
                    kb.slug,
                    kb.tenant_id,
                    name,
                    mime,
                    kind,
                    size,
                    object_key,
                    kb.collection,
                    backend,
                    metadata.title,
                    metadata.publisher,
                    metadata.source_url,
                    metadata.published_on,
                    metadata.supersedes,
                    actor.user_id,
                    actor.username,
                    actor.name,
                    sha256,
                    metadata_missing,
                ),
            )
            return _doc(await cursor.fetchone())

    async def update_metadata(
        self, tenant_id: str, slug: str, document_id: str, metadata: SourceMetadata
    ) -> KbDocumentRecord | None:
        """Corrects a document's D3 metadata; None when the live KB holds no such document."""
        async with self._db.connection() as conn, conn.transaction():
            await conn.execute(_KB_LOCK, (slug,))
            if await self._document(conn, tenant_id, slug, document_id) is None:
                return None
            await self._check_supersedes(conn, slug, document_id, metadata.supersedes)
            cursor = await conn.execute(
                "UPDATE agent.kb_documents SET title = %s, publisher = %s, source_url = %s, published_on = %s, "
                # An administrator's correction is the metadata: the flag of a seeded file clears (ADR-0392).
                f"supersedes = %s, metadata_missing = false, updated_at = now() WHERE id = %s RETURNING {_DOC_COLUMNS}",
                (
                    metadata.title,
                    metadata.publisher,
                    metadata.source_url,
                    metadata.published_on,
                    metadata.supersedes,
                    document_id,
                ),
            )
            return _doc(await cursor.fetchone())

    async def _document(self, conn, tenant_id: str, slug: str, document_id: str) -> KbDocumentRecord | None:
        cursor = await conn.execute(
            f"SELECT {_DOC_COLUMNS} FROM agent.kb_documents d WHERE d.id = %s AND d.kb_slug = %s "
            "AND d.tenant_id = %s AND d.purged_at IS NULL "
            "AND EXISTS (SELECT 1 FROM agent.kbs k WHERE k.slug = d.kb_slug AND k.deleted_at IS NULL)",
            (document_id, slug, tenant_id),
        )
        row = await cursor.fetchone()
        return _doc(row) if row else None

    async def _check_supersedes(self, conn, slug: str, document_id: str | None, supersedes: str | None) -> None:
        if supersedes is None:
            return
        if supersedes == document_id:
            raise InvalidSupersedes("a document cannot supersede itself")
        cursor = await conn.execute(
            "SELECT id::text AS id, supersedes::text AS supersedes FROM agent.kb_documents "
            "WHERE kb_slug = %s AND purged_at IS NULL",
            (slug,),
        )
        chain = {row["id"]: row["supersedes"] for row in await cursor.fetchall()}
        if supersedes not in chain:
            raise InvalidSupersedes("supersedes names no document of this knowledge base")
        if any(other != document_id and earlier == supersedes for other, earlier in chain.items()):
            raise InvalidSupersedes("another document already supersedes that one")
        # Following the earlier versions from the target must never reach this document.
        seen, at = set(), supersedes
        while at is not None and at not in seen:
            if at == document_id:
                raise InvalidSupersedes("that would make a loop of versions")
            seen.add(at)
            at = chain.get(at)

    async def list_documents(self, tenant_id: str, slug: str) -> list[KbDocumentRecord]:
        """A live KB's documents, oldest first; none for a deleted or another tenant's KB."""
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                f"SELECT {_DOC_COLUMNS} FROM agent.kb_documents d WHERE d.kb_slug = %s AND d.tenant_id = %s "
                "AND d.purged_at IS NULL "
                "AND EXISTS (SELECT 1 FROM agent.kbs k WHERE k.slug = d.kb_slug AND k.deleted_at IS NULL) "
                "ORDER BY d.created_at, d.id",
                (slug, tenant_id),
            )
            return [_doc(row) for row in await cursor.fetchall()]

    async def get_document(self, tenant_id: str, slug: str, document_id: str) -> KbDocumentRecord | None:
        async with self._db.connection() as conn:
            return await self._document(conn, tenant_id, slug, document_id)

    async def indexed_documents(self, tenant_id: str, slugs) -> list[KbDocumentRecord]:
        """The indexed documents of the tenant's live KBs among `slugs`: what retrieval may return
        and how its versions chain (D3)."""
        wanted = [s for s in dict.fromkeys(slugs) if isinstance(s, str) and _SLUG.match(s)]
        if not wanted:
            return []
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                f"SELECT {_DOC_COLUMNS} FROM agent.kb_documents d WHERE d.tenant_id = %s AND d.kb_slug = ANY(%s) "
                "AND d.status = 'indexed' AND d.purged_at IS NULL "
                "AND EXISTS (SELECT 1 FROM agent.kbs k WHERE k.slug = d.kb_slug AND k.deleted_at IS NULL)",
                (tenant_id, wanted),
            )
            return [_doc(row) for row in await cursor.fetchall()]

    async def remember_status(
        self,
        document_id: str,
        *,
        status: AttachmentStatus,
        reason: FailureReason | None,
        pages: int | None,
        warnings: list[dict[str, Any]],
    ) -> None:
        """Keeps the status another backend last reported (ADR-0233); the internal backend's records
        are its status already."""
        async with self._db.connection() as conn:
            await conn.execute(
                "UPDATE agent.kb_documents SET status = %s, reason = %s, pages = %s, warnings = %s, "
                "indexed_at = CASE WHEN %s = 'indexed' THEN coalesce(indexed_at, now()) ELSE indexed_at END, "
                "updated_at = now() WHERE id = %s AND backend <> 'internal' "
                "AND reason IS DISTINCT FROM 'register_failed'",
                (status, reason, pages, Jsonb(warnings), status, document_id),
            )

    # ------------------------------------------------------------------ ingest service and sweeper only

    async def by_id(self, document_id: str) -> KbDocumentRecord | None:
        async with self._db.connection() as conn:
            cursor = await conn.execute(f"SELECT {_DOC_COLUMNS} FROM agent.kb_documents WHERE id = %s", (document_id,))
            row = await cursor.fetchone()
        return _doc(row) if row else None

    async def start_attempt(self, document_id: str) -> KbDocumentRecord | None:
        """Counts an ingestion attempt of a pending document of a live KB; None otherwise."""
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "UPDATE agent.kb_documents SET attempts = attempts + 1, updated_at = now() "
                "WHERE id = %s AND status = 'pending' AND purged_at IS NULL "
                f"RETURNING {_DOC_COLUMNS}",
                (document_id,),
            )
            row = await cursor.fetchone()
        return _doc(row) if row else None

    async def mark_indexed(
        self, document_id: str, *, pages: int | None, chunks: int, conn, warnings: list[dict[str, Any]] | None = None
    ) -> bool:
        """False when the document is no longer pending or its KB was deleted meanwhile: the job's
        chunks are then rolled back with this transaction."""
        cursor = await conn.execute(
            "UPDATE agent.kb_documents SET status = 'indexed', reason = NULL, pages = %s, chunks = %s, "
            "warnings = %s, indexed_at = now(), updated_at = now() "
            "WHERE id = %s AND status = 'pending' AND purged_at IS NULL",
            (pages, chunks, Jsonb(warnings or []), document_id),
        )
        return cursor.rowcount == 1

    async def mark_failed(self, document_id: str, reason: FailureReason, *, pages: int | None = None) -> bool:
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "UPDATE agent.kb_documents SET status = 'failed', reason = %s, pages = %s, updated_at = now() "
                "WHERE id = %s AND status = 'pending'",
                (reason, pages, document_id),
            )
            return cursor.rowcount == 1

    async def mark_register_failed(self, document_id: str) -> KbDocumentRecord | None:
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "UPDATE agent.kb_documents SET status = 'failed', reason = 'register_failed', updated_at = now() "
                f"WHERE id = %s AND (status = 'pending' OR backend <> 'internal') RETURNING {_DOC_COLUMNS}",
                (document_id,),
            )
            row = await cursor.fetchone()
        return _doc(row) if row else None

    async def pending_ids(self) -> list[str]:
        """The internal backend's pending documents of live KBs."""
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT id::text AS id FROM agent.kb_documents "
                "WHERE status = 'pending' AND backend = 'internal' AND purged_at IS NULL ORDER BY created_at"
            )
            return [row["id"] for row in await cursor.fetchall()]

    async def known_keys(self, keys: list[str]) -> set[str]:
        """The object keys among `keys` that a KB document names (the orphan sweeper)."""
        if not keys:
            return set()
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT object_key FROM agent.kb_documents WHERE object_key = ANY(%s)", (keys,)
            )
            return {row["object_key"] for row in await cursor.fetchall()}
