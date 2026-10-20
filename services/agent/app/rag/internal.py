"""InternalDocumentBackend (C6): the phase 04 pipeline behind the DocumentBackend protocol.
Registering enqueues the attachment for the ingest service; status is the attachment
record's; search is the hybrid retrieval of app/rag/retrieve.py over the indexed files of the
scope's session collections; fetch reads chunk texts from the VectorStore. Every call names the
scope's owner and only its collections (I1), so another user's or another conversation's chunks
are never returned, whatever reference a call names. Knowledge base collections hold nothing in
this backend until the kb capability (phase 08) gives KB documents a record of their own."""

import asyncio
from collections.abc import Sequence

from app.attachments.store import AttachmentStore
from app.rag.backend import DocumentScope, DocumentStatus, DocumentWarning, SourceHit, StoredFile
from app.rag.collections import session_collection
from app.rag.ingest.queue import IngestQueue
from app.rag.retrieve import HybridRetriever
from app.rag.vectorstore.base import VectorStore


class InternalDocumentBackend:
    name = "internal"

    def __init__(
        self,
        *,
        attachments: AttachmentStore,
        vectors: VectorStore,
        retriever: HybridRetriever,
        queue: IngestQueue,
    ):
        self._attachments = attachments
        self._vectors = vectors
        self._retriever = retriever
        self._queue = queue

    async def register(self, stored: StoredFile) -> None:
        await self._queue.enqueue(stored.id)

    async def status(self, document_id: str, scope: DocumentScope) -> DocumentStatus | None:
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
        hits = [hit for found in await asyncio.gather(*searches) for hit in found]
        return sorted(hits, key=lambda h: -h.score)[:k] if len(searches) > 1 else hits

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
        return texts
