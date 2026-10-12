"""InternalDocumentBackend (C6): the phase 04 pipeline behind the DocumentBackend protocol.
Registering enqueues the attachment for the ingest service; status is the attachment
record's; search is the hybrid retrieval of app/rag/retrieve.py over the conversation's
indexed files; fetch reads chunk texts from the VectorStore. Every call names the scope's
owner and conversation, so another user's or another conversation's chunks are never
returned, whatever reference a call names."""

from collections.abc import Sequence

from app.attachments.store import AttachmentStore, collection_for
from app.rag.backend import DocumentScope, DocumentStatus, SourceHit, StoredFile
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
        record = await self._attachments.get(scope.owner, scope.conversation_id, document_id)
        if record is None:
            return None
        return DocumentStatus(status=record.status, reason=record.reason, pages=record.pages)

    async def search(self, query: str, scope: DocumentScope, k: int) -> list[SourceHit]:
        records = await self._attachments.list_for(scope.owner, scope.conversation_id)
        names = {r.id: r.name for r in records if r.status == "indexed"}
        if not names:
            return []
        hits = await self._retriever.retrieve(
            scope.owner,
            collection_for(scope.conversation_id),
            query,
            document_ids=list(names),
            top_k=k,
        )
        return [
            SourceHit(
                ref=h.id,
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
        return await self._vectors.fetch(scope.owner, collection_for(scope.conversation_id), refs)
