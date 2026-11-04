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

A delete (decision 1, ADR-0444, ADR-0448) removes the record, and with it (ON DELETE CASCADE) every chunk of
the file in the index; and the recorded context packages that name it. The gateway, which holds the uploads
bucket's write key, then deletes the stored object (ADR-0450); the agent holds none. It leaves a
tombstone: a package, a history citation or evidence naming the file is not written afterwards, and one purge
pass after the longest turn deadline removes what a turn in flight recorded meanwhile. Later turns of its
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

from app.attachments.store import _COLUMNS, AttachmentRecord, _record
from app.context.log import file_ids
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


class DeleteConfig(_Strict):
    purge_every_s: float = Field(gt=0, le=3600)
    purge_batch: int = Field(ge=1, le=1000)


class FileLibraryConfig(_Strict):
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    list: ListConfig
    search: SearchConfig
    read: ReadConfig
    delete: DeleteConfig


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
        purge_after_s: float = 0.0,
    ):
        """`vectors`, `retriever`: the internal backend's chunk store and hybrid retriever (app/rag), for
        search and read. None in a test that needs only the list.
        `purge_after_s`: the longest a turn can still be running (the longest turn deadline and the gateway's
        margin); a deleted file's purge pass runs this long after the delete (ADR-0448)."""
        self._db = db
        self.config = config
        self._vectors = vectors
        self._retriever = retriever
        self.purge_after_s = purge_after_s

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
        """Deletes one of the owner's files of `conversation_id` (decision 1, ADR-0444, ADR-0448): its tombstone,
        the context packages naming it, then the record and, by the foreign key, its chunks, in one transaction.
        None, and nothing deleted, for an id the owner does not have in that conversation. The gateway deletes
        the stored object afterwards (ADR-0450)."""
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
            # The tombstone (ADR-0448): what a turn in flight writes after this is dropped, and the purge pass
            # once every such turn has ended removes what slipped past.
            await conn.execute(
                "INSERT INTO agent.deleted_attachments (id, tenant_id, user_id, purge_after) "
                "VALUES (%s, %s, %s, now() + make_interval(secs => %s)) ON CONFLICT (id) DO NOTHING",
                (attachment_id, owner.tenant_id, owner.user_id, self.purge_after_s),
            )
            await conn.execute(
                "DELETE FROM agent.context_packages WHERE attachment_ids @> ARRAY[%s]::uuid[]",
                (attachment_id,),
            )
            await conn.execute(
                "DELETE FROM agent.attachments WHERE id = %s AND tenant_id = %s AND user_id = %s",
                (attachment_id, owner.tenant_id, owner.user_id),
            )
        return record

    async def deleted(self, ids: Sequence[str]) -> set[str]:
        """The ids among `ids` of files a user deleted (their tombstones, ADR-0448): a context package, a
        history citation or the turn's evidence naming one is dropped."""
        wanted = file_ids(ids)
        if not wanted:
            return set()
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT id::text AS id FROM agent.deleted_attachments WHERE id = ANY(%s::uuid[])", (wanted,)
            )
            return {row["id"] for row in await cursor.fetchall()}

    async def purge_due(self) -> int:
        """One purge pass (ADR-0448): for at most `delete.purge_batch` deleted files whose `purge_after` has
        passed, removes every context package that names one (a turn in flight at the delete may have recorded
        it) and marks them purged. A file is purged once; a second agent process skips the rows this one holds.
        Returns the files purged."""
        async with self._db.connection() as conn, conn.transaction():
            cursor = await conn.execute(
                "SELECT id FROM agent.deleted_attachments WHERE purged_at IS NULL AND purge_after <= now() "
                "ORDER BY purge_after LIMIT %s FOR UPDATE SKIP LOCKED",
                (self.config.delete.purge_batch,),
            )
            due = [row["id"] for row in await cursor.fetchall()]
            if not due:
                return 0
            await conn.execute(
                "DELETE FROM agent.context_packages WHERE attachment_ids && %s::uuid[]", (due,)
            )
            await conn.execute(
                "UPDATE agent.deleted_attachments SET purged_at = now() WHERE id = ANY(%s::uuid[])", (due,)
            )
        return len(due)

    async def run_purges(self) -> None:
        """The purge pass every `delete.purge_every_s`, for the life of the process; a database that cannot
        answer is tried again at the next pass."""
        while True:
            try:
                purged = await self.purge_due()
                if purged:
                    log.info("purged the context packages of %d deleted file(s)", purged)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("deleted files' purge pass failed: %s", type(exc).__name__)
            await asyncio.sleep(self.config.delete.purge_every_s)

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
