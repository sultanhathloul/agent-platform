"""A test-only DocumentBackend (C6): in memory, one chunk per page, scored by word overlap
with the query after Arabic normalization. It proves that the docs capability depends on the
DocumentBackend protocol and nothing else: its tests pass on this backend and on the
internal one, with no change to capability code. `index` and `fail` stand in for whatever
ingestion a real backend runs."""

import dataclasses
import uuid
from collections.abc import Sequence

from app.attachments.service import AttachmentService
from app.rag.backend import DocumentScope, DocumentStatus, SourceHit, StoredFile
from app.rag.types import FailureReason
from app.text.arabic import search_terms


@dataclasses.dataclass
class _Chunk:
    ref: str
    page: int | None
    section: str | None
    text: str


@dataclasses.dataclass
class _File:
    stored: StoredFile
    status: DocumentStatus
    chunks: list[_Chunk] = dataclasses.field(default_factory=list)

    def within(self, scope: DocumentScope) -> bool:
        s = self.stored
        return (s.tenant_id, s.user_id, s.conversation_id) == (
            scope.tenant_id,
            scope.user_id,
            scope.conversation_id,
        )


class FakeDocumentBackend:
    name = "fake"

    def __init__(self) -> None:
        self._files: dict[str, _File] = {}

    async def register(self, stored: StoredFile) -> None:
        self._files[stored.id] = _File(stored, DocumentStatus(status="pending"))

    def index(self, document_id: str, pages: Sequence[str], *, sections: Sequence[str | None] = ()) -> None:
        """Indexes one chunk per page; with `sections`, chunks have sections and no pages."""
        file = self._files[document_id]
        file.chunks = [
            _Chunk(
                ref=str(uuid.uuid4()),
                page=None if sections else number,
                section=sections[number - 1] if sections else None,
                text=text,
            )
            for number, text in enumerate(pages, start=1)
        ]
        file.status = DocumentStatus(status="indexed", pages=None if sections else len(pages))

    def fail(self, document_id: str, reason: FailureReason) -> None:
        self._files[document_id].status = DocumentStatus(status="failed", reason=reason)

    async def status(self, document_id: str, scope: DocumentScope) -> DocumentStatus | None:
        file = self._files.get(document_id)
        return file.status if file is not None and file.within(scope) else None

    async def search(self, query: str, scope: DocumentScope, k: int) -> list[SourceHit]:
        wanted = set(search_terms(query))
        scored = []
        for file in self._files.values():
            if not file.within(scope) or file.status.status != "indexed":
                continue
            for order, chunk in enumerate(file.chunks):
                overlap = len(wanted & set(search_terms(chunk.text)))
                if overlap:
                    scored.append((-overlap, file.stored.id, order, file, chunk))
        scored.sort(key=lambda item: item[:3])
        return [
            SourceHit(
                ref=chunk.ref,
                document_id=file.stored.id,
                file_name=file.stored.name,
                page=chunk.page,
                section=chunk.section,
                text=chunk.text,
                score=float(-negative),
            )
            for negative, _, _, file, chunk in scored[:k]
        ]

    async def fetch(self, refs: Sequence[str], scope: DocumentScope) -> dict[str, str]:
        wanted = set(refs)
        return {
            chunk.ref: chunk.text
            for file in self._files.values()
            if file.within(scope)
            for chunk in file.chunks
            if chunk.ref in wanted
        }


def with_backend(services, backend):
    """The session's services with `backend` serving documents: the attachment records stay
    in Postgres, status and retrieval come from `backend`."""
    attachments = services.attachments
    swapped = AttachmentService(
        db=attachments.db,
        store=attachments.store,
        objects=attachments.objects,
        vectors=attachments.vectors,
        documents=backend,
        worker=attachments.worker,
        config=attachments.config,
    )
    return dataclasses.replace(services, documents=backend, attachments=swapped)
