"""InternalDocumentBackend (C6): the phase 04 pipeline behind the DocumentBackend protocol.
Registering enqueues the attachment for the ingest service; status is the attachment
record's; search is the hybrid retrieval of app/rag/retrieve.py over the indexed files of the
scope's session collections; fetch reads chunk texts from the VectorStore. Every call names the
scope's owner and only its collections (I1), so another user's or another conversation's chunks
are never returned, whatever reference a call names.

Knowledge base collections (phase 08, I2, ADR-0334): a KB document has a record of its own
(app/kb/store.py), owned by its KB's tenant with no user; registering enqueues it as
`kb:<id>`; status is its record's; search covers the scope's KB collections in one hybrid search
over their indexed documents (cross-KB retrieval, I4), with the latest version of a source preferred
before rerank (D3, app/rag/versions.py); `purge` deletes a deleted KB's chunks (I1)."""

import asyncio
from collections.abc import Sequence

from app.attachments.store import AttachmentStore
from app.rag.backend import DocumentScope, DocumentStatus, DocumentWarning, SourceHit, StoredFile
from app.rag.collections import kb_collection, kind_of, session_collection
from app.rag.ingest.queue import IngestQueue, kb_item
from app.rag.retrieve import HybridRetriever
from app.rag.types import Owner
from app.rag.vectorstore.base import VectorStore
from app.rag.versions import newer_versions, prefer_latest

_KB_PREFIX = "kb_"


def kb_slug_of(collection: str) -> str | None:
    return collection[len(_KB_PREFIX) :] if kind_of(collection) == "kb" else None


class InternalDocumentBackend:
    name = "internal"

    def __init__(
        self,
        *,
        attachments: AttachmentStore,
        vectors: VectorStore,
        retriever: HybridRetriever,
        queue: IngestQueue,
        kbs=None,
        superseded_keep: int = 1,
    ):
        """`kbs`: the KB store (app/kb/store.py), or None on a platform without knowledge bases.
        `superseded_keep`: how many chunks of an older version stay beside its latest (D3)."""
        self._attachments = attachments
        self._vectors = vectors
        self._retriever = retriever
        self._queue = queue
        self._kbs = kbs
        self._keep = superseded_keep

    async def register(self, stored: StoredFile) -> None:
        if kind_of(stored.collection) == "kb":
            await self._queue.enqueue(kb_item(stored.id))
            return
        await self._queue.enqueue(stored.id)

    async def status(self, document_id: str, scope: DocumentScope) -> DocumentStatus | None:
        kbs = [slug for c in scope.collections if (slug := kb_slug_of(c))]
        if kbs and self._kbs is not None:
            for slug in kbs:
                record = await self._kbs.get_document(scope.tenant_id, slug, document_id)
                if record is not None:
                    return DocumentStatus(
                        status=record.status,
                        reason=record.reason,
                        pages=record.pages,
                        warnings=[DocumentWarning.model_validate(w) for w in record.warnings],
                    )
        for conversation in scope.conversations:
            record = await self._attachments.get(scope.owner, conversation, document_id)
            if record is not None:
                warnings = [DocumentWarning.model_validate(w) for w in record.warnings]
                return DocumentStatus(
                    status=record.status, reason=record.reason, pages=record.pages, warnings=warnings
                )
        return None

    async def search(self, query: str, scope: DocumentScope, k: int) -> list[SourceHit]:
        searches = [
            self._search_session(query, scope, conversation, k) for conversation in scope.conversations
        ]
        kbs = [slug for c in scope.collections if (slug := kb_slug_of(c))]
        if kbs and self._kbs is not None:
            searches.append(self._search_kbs(query, scope, kbs, k))
        hits = [hit for found in await asyncio.gather(*searches) for hit in found]
        return sorted(hits, key=lambda h: -h.score)[:k] if len(searches) > 1 else hits

    async def _search_kbs(self, query: str, scope: DocumentScope, slugs: list[str], k: int) -> list[SourceHit]:
        """One hybrid search across the scope's KB collections, over their indexed documents of
        live KBs, the latest version of a source first (D3)."""
        documents = await self._kbs.indexed_documents(scope.tenant_id, slugs)
        titles = {d.id: d.title for d in documents}
        if not titles:
            return []
        newer = newer_versions({d.id: d.supersedes for d in documents if d.supersedes in titles})
        collections = sorted({kb_collection(d.kb_slug) for d in documents})

        def prefer(chunks):
            return prefer_latest(chunks, lambda c: c.attachment_id, newer, keep=self._keep)

        hits = await self._retriever.retrieve(
            Owner(tenant_id=scope.tenant_id, user_id=""),
            collections,
            query,
            document_ids=list(titles),
            top_k=k,
            prefer=prefer,
        )
        return [
            SourceHit(
                ref=h.id,
                collection=h.collection or "",
                document_id=h.attachment_id,
                file_name=titles[h.attachment_id],
                page=h.page,
                section=h.section,
                text=h.text,
                score=h.score,
            )
            for h in hits
            if h.attachment_id in titles and h.collection in collections
        ]

    async def _search_session(
        self, query: str, scope: DocumentScope, conversation: str, k: int
    ) -> list[SourceHit]:
        records = await self._attachments.list_for(scope.owner, conversation)
        names = {r.id: r.name for r in records if r.status == "indexed"}
        if not names:
            return []
        collection = session_collection(conversation)
        hits = await self._retriever.retrieve(
            scope.owner, collection, query, document_ids=list(names), top_k=k
        )
        return [
            SourceHit(
                ref=h.id,
                collection=collection,
                document_id=h.attachment_id,
                file_name=names[h.attachment_id],
                page=h.page,
                section=h.section,
                text=h.text,
                score=h.score,
            )
            for h in hits
            if h.attachment_id in names
        ]

    async def fetch(self, refs: Sequence[str], scope: DocumentScope) -> dict[str, str]:
        texts: dict[str, str] = {}
        for conversation in scope.conversations:
            texts |= await self._vectors.fetch(scope.owner, session_collection(conversation), refs)
        kb_owner = Owner(tenant_id=scope.tenant_id, user_id="")
        for collection in scope.collections:
            if kind_of(collection) == "kb":
                texts |= await self._vectors.fetch(kb_owner, collection, refs)
        return texts

    async def purge(self, collection: str, scope: DocumentScope) -> int:
        """Deletes every chunk of a knowledge base collection of the scope (a deleted KB, I1)."""
        if kind_of(collection) != "kb" or collection not in scope.collections:
            return 0
        return await self._vectors.purge(Owner(tenant_id=scope.tenant_id, user_id=""), collection)
