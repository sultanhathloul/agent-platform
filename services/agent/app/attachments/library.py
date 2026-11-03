"""My files (my-files PR, owner decisions 1, 2 and 5; ADR-0442 to ADR-0445): every file a user uploaded in any
of their conversations, as the page «ملفاتي» lists it and as the `files` capability's tools search and read
it.

Only conversation uploads (agent.attachments) are files here; a knowledge base's documents live in their own
records and are never listed or searched (decision 1). Every method names the owner (tenant, user) in its
WHERE,
so no request lists, searches, reads or deletes another user's file, whatever id it sends (G6): another user's
file is the same nothing as one that does not exist.

Search reuses the phase 04 index: one hybrid search (one embedding, vector and full-text top k, reciprocal
rank
fusion) over the session collections of the owner's newest indexed files on the internal backend. A file
another backend indexed (an MCP RAG server, I11) is listed but not searched across conversations: that server
searches one conversation's collection per call, and no backend call spans the owner's whole library
(ADR-0443). What search and read return are references (chunk id, collection, page): the answer step fetches
the text through the backend under the turn's scope and POST_TOOL checks it there, as for every document
source (S4, ADR-0159).

A delete (decision 1, ADR-0444) removes the record, and with it (ON DELETE CASCADE) every chunk of the file in
the index; the recorded context packages that hold its chunks; and the stored object. Later turns of its
conversation no longer see it (the attachments node reads the records), and the history shows neither its chip
nor its citations. Deleting a file never deletes a conversation; nothing deletes a conversation (decision 5,
ADR-0166)."""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.attachments.objects import ObjectStore, StorageUnavailable
from app.attachments.store import _COLUMNS, AttachmentRecord, _record
from app.db import Database
from app.rag.collections import session_collection
from app.rag.types import Owner
from app.text.arabic import normalize

log = logging.getLogger(__name__)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ListConfig(_Strict):
    page_size: int = Field(ge=1, le=100)
    max_page: int = Field(ge=1, le=500)
    scan_max: int = Field(ge=10, le=20000)
    max_query_chars: int = Field(ge=10, le=2000)


class SearchConfig(_Strict):
    max_files: int = Field(ge=1, le=5000)
    top_k: int = Field(ge=1, le=20)
    max_k: int = Field(ge=1, le=20)


class ReadConfig(_Strict):
    max_chunks: int = Field(ge=1, le=50)
    max_pages: int = Field(ge=1, le=100)


class FileLibraryConfig(_Strict):
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    list: ListConfig
    search: SearchConfig
    read: ReadConfig


def load_file_library_config(path: Path) -> FileLibraryConfig:
    return FileLibraryConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


@dataclass(frozen=True)
class FileEntry:
    """One of the owner's files with the conversation it was uploaded in."""

    record: AttachmentRecord
    conversation_title: str | None
    conversation_created_at: datetime | None


@dataclass(frozen=True)
class FileChunk:
    """A chunk of one of the owner's files, as a reference: the answer step fetches its text."""

    ref: str
    collection: str
    page: int | None
    section: str | None
    score: float


@dataclass(frozen=True)
class FileHit:
    entry: FileEntry
    chunk: FileChunk


def fold(text: str) -> str:
    """The text a name search compares: Arabic-normalized (alef, yaa, taa marbuta, tatweel, diacritics) and
    lower-cased."""
    return normalize(text).casefold()


def matches(entry: FileEntry, words: Sequence[str]) -> bool:
    """Every word of the query is in the file's name or its conversation's title."""
    haystack = fold(f"{entry.record.name} {entry.conversation_title or ''}")
    return all(word in haystack for word in words)


# The record's columns, qualified: the list joins the conversation's ownership row.
_ENTRY_COLUMNS = ", ".join(f"a.{c.strip()}" for c in _COLUMNS.split(","))


def _entry(row) -> FileEntry:
    data = dict(row)
    title = data.pop("conversation_title", None)
    created = data.pop("conversation_created_at", None)
    return FileEntry(record=_record(data), conversation_title=title or None, conversation_created_at=created)


class FileLibrary:
    def __init__(
        self,
        db: Database,
        config: FileLibraryConfig,
        *,
        vectors=None,
        retriever=None,
        objects: ObjectStore | None = None,
    ):
        """`vectors`, `retriever`: the internal backend's chunk store and hybrid retriever (app/rag), for
        search and read; `objects`: the uploads bucket, for a delete. None in a test that needs only the list."""
        self._db = db
        self.config = config
        self._vectors = vectors
        self._retriever = retriever
        self._objects = objects

    # ------------------------------------------------------------------ the page (decision 1)

    async def _select(self, owner: Owner, where: str, params: Sequence, limit: int) -> list[FileEntry]:
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                f"SELECT {_ENTRY_COLUMNS}, o.title AS conversation_title, "
                "o.created_at AS conversation_created_at "
                "FROM agent.attachments a "
                # The conversation's title, only when the owner's own record says it is theirs.
                "LEFT JOIN agent.conversation_owners o ON o.conversation_id = a.conversation_id "
                "AND o.tenant_id = a.tenant_id AND o.user_id = a.user_id "
                f"WHERE a.tenant_id = %s AND a.user_id = %s{where} "
                "ORDER BY a.created_at DESC, a.id DESC LIMIT %s",
                (owner.tenant_id, owner.user_id, *params, limit),
            )
            return [_entry(row) for row in await cursor.fetchall()]

    async def page(
        self,
        owner: Owner,
        *,
        query: str = "",
        kinds: Sequence[str] = (),
        status: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        before: datetime | None = None,
        limit: int | None = None,
    ) -> tuple[list[FileEntry], datetime | None]:
        """The owner's files, newest first, and `before` of the next page (None on the last). With a query,
        the best `limit` matches among the newest `scan_max` files and no next page."""
        bounds = self.config.list
        size = max(1, min(limit or bounds.page_size, bounds.max_page))
        where, params = "", []
        if kinds:
            where += " AND a.kind = ANY(%s)"
            params.append(list(kinds))
        if status is not None:
            where += " AND a.status = %s"
            params.append(status)
        if since is not None:
            where += " AND a.created_at >= %s"
            params.append(since)
        if until is not None:
            where += " AND a.created_at < %s"
            params.append(until)
        words = [w for w in fold(query[: bounds.max_query_chars]).split() if w]
        if words:
            scanned = await self._select(owner, where, params, bounds.scan_max)
            # Linear and capped, in a worker thread so the event loop stays free (rule 12d).
            found = await asyncio.to_thread(lambda: [e for e in scanned if matches(e, words)][:size])
            return found, None
        if before is not None:
            where += " AND a.created_at < %s"
            params.append(before)
        found = await self._select(owner, where, params, size + 1)
        page = found[:size]
        return page, (page[-1].record.created_at if len(found) > size else None)

    async def get(self, owner: Owner, attachment_id: str) -> FileEntry | None:
        """One of the owner's files, in whichever conversation; None for another user's id."""
        found = await self._select(owner, " AND a.id = %s", [attachment_id], 1)
        return found[0] if found else None

    async def existing(self, owner: Owner, ids: Sequence[str]) -> set[str]:
        """The ids among `ids` that are the owner's files now (a deleted file is gone)."""
        wanted = list(dict.fromkeys(i for i in ids if isinstance(i, str)))
        if not wanted:
            return set()
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT id::text AS id FROM agent.attachments "
                "WHERE tenant_id = %s AND user_id = %s AND id::text = ANY(%s)",
                (owner.tenant_id, owner.user_id, wanted),
            )
            return {row["id"] for row in await cursor.fetchall()}

    async def delete(self, owner: Owner, conversation_id: str, attachment_id: str) -> AttachmentRecord | None:
        """Deletes one of the owner's files of `conversation_id` (decision 1, ADR-0444): the context packages
        holding its chunks, then the record and, by the foreign key, its chunks, in one transaction; then the
        stored object. None, and nothing deleted, for an id the owner does not have in that conversation.
        An object the storage cannot delete now is left to the orphan sweeper, which removes any object no
        record names."""
        async with self._db.connection() as conn, conn.transaction():
            cursor = await conn.execute(
                f"SELECT {_COLUMNS} FROM agent.attachments "
                "WHERE id = %s AND tenant_id = %s AND user_id = %s AND conversation_id = %s FOR UPDATE",
                (attachment_id, owner.tenant_id, owner.user_id, conversation_id),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            record = _record(row)
            await conn.execute(
                "DELETE FROM agent.context_packages WHERE attachment_ids @> ARRAY[%s]::uuid[]",
                (attachment_id,),
            )
            await conn.execute(
                "DELETE FROM agent.attachments WHERE id = %s AND tenant_id = %s AND user_id = %s",
                (attachment_id, owner.tenant_id, owner.user_id),
            )
        if self._objects is not None:
            try:
                await self._objects.delete(record.object_key)
            except StorageUnavailable:
                log.warning("file %s deleted; its object is left to the orphan sweeper", attachment_id)
        return record

    # ------------------------------------------------------------------ the tools (decision 2)

    async def searchable(self, owner: Owner) -> list[FileEntry]:
        """The owner's newest indexed files on the internal backend, at most `search.max_files`."""
        return await self._select(
            owner, " AND a.status = 'indexed' AND a.backend = 'internal'", [], self.config.search.max_files
        )

    async def search(self, owner: Owner, query: str, k: int) -> list[FileHit]:
        """At most `k` (≤ `search.max_k`) chunks of the owner's searchable files, best first, in one hybrid
        search across their conversations."""
        if self._retriever is None:
            return []
        files = {e.record.id: e for e in await self.searchable(owner)}
        if not files:
            return []
        collections = sorted({session_collection(e.record.conversation_id) for e in files.values()})
        found = await self._retriever.retrieve(
            owner,
            collections,
            query,
            document_ids=list(files),
            top_k=min(k, self.config.search.max_k),
            # The best chunks, whichever conversation holds them: no per-conversation floor.
            min_per_collection=0,
        )
        return [
            FileHit(
                entry=files[c.attachment_id],
                chunk=FileChunk(
                    ref=c.id, collection=c.collection or "", page=c.page, section=c.section, score=c.score
                ),
            )
            for c in found
            if c.attachment_id in files
        ]

    async def read(
        self, owner: Owner, attachment_id: str, pages: Sequence[int] | None
    ) -> tuple[FileEntry, list[FileChunk]] | None:
        """One of the owner's indexed files and its chunks in page order, those of `pages` only when named, at
        most `read.max_chunks`. None for a file that is not the owner's, or not indexed on the internal
        backend."""
        entry = await self.get(owner, attachment_id)
        if entry is None or self._vectors is None:
            return None
        record = entry.record
        if record.status != "indexed" or record.backend != "internal":
            return entry, []
        wanted = sorted(set(pages or ()))[: self.config.read.max_pages] or None
        chunks = await self._vectors.document_chunks(
            owner, record.collection, record.id, pages=wanted, limit=self.config.read.max_chunks
        )
        return entry, [
            FileChunk(
                ref=c["id"], collection=record.collection, page=c["page"], section=c["section"], score=0.0
            )
            for c in chunks
        ]

    async def collections(self, owner: Owner) -> tuple[str, ...]:
        """The session collections of the owner's searchable files: what the evidence step may fetch from for
        the `files` capability (ADR-0443)."""
        return tuple(
            sorted({session_collection(e.record.conversation_id) for e in await self.searchable(owner)})
        )


class FilesFacade:
    """The caller's file library as a tool sees it (ADR-0443): bound by the broker to the verified principal
    and the turn's conversation; no argument names an owner."""

    def __init__(self, library: FileLibrary, *, tenant_id: str, user_id: str, conversation_id: str):
        self._library = library
        self._owner = Owner(tenant_id=tenant_id, user_id=user_id)
        self.conversation_id = conversation_id

    @property
    def config(self) -> FileLibraryConfig:
        return self._library.config

    async def search(self, query: str, k: int) -> list[FileHit]:
        return await self._library.search(self._owner, query, k)

    async def read(self, attachment_id: str, pages: Sequence[int] | None):
        return await self._library.read(self._owner, attachment_id, pages)
