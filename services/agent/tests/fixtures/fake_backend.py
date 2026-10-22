"""A test-only DocumentBackend (C6): in memory, one chunk per page, scored by word overlap
with the query after Arabic normalization. It proves that the docs capability depends on the
DocumentBackend protocol and nothing else: its tests pass on this backend and on the
internal one, with no change to capability code. `index` and `fail` stand in for whatever
ingestion a real backend runs.

It also holds knowledge base collections (I1): `add_kb` puts a tenant's document in `kb_{slug}`,
which a scope reaches only when the broker put that collection in it. A session file is its
user's; a knowledge base document is its tenant's."""

import dataclasses
import uuid
from collections.abc import Sequence

from app.attachments.service import AttachmentService
from app.rag.access import DocumentAccess
from app.rag.backend import DocumentScope, DocumentStatus, DocumentWarning, SourceHit, StoredFile
from app.rag.collections import kb_collection, kind_of
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
        if s.collection not in scope.collections or s.tenant_id != scope.tenant_id:
            return False
        # A knowledge base is its tenant's; a session collection is one user's.
        return kind_of(s.collection) == "kb" or s.user_id == scope.user_id


class FakeDocumentBackend:
    name = "fake"

    def __init__(self) -> None:
        self._files: dict[str, _File] = {}

    async def register(self, stored: StoredFile) -> None:
        self._files[stored.id] = _File(stored, DocumentStatus(status="pending"))

    def add_kb(self, tenant_id: str, slug: str, name: str, pages: Sequence[str]) -> str:
        """A tenant's knowledge base document in `kb_{slug}`, indexed; returns its id."""
        document_id = str(uuid.uuid4())
        stored = StoredFile(
            id=document_id,
            conversation_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            user_id="kb-admin",
            collection=kb_collection(slug),
            name=name,
            mime="application/pdf",
            kind="pdf",
            size=1,
            object_key=f"kb/{slug}/{document_id}",
        )
        self._files[document_id] = _File(stored, DocumentStatus(status="pending"))
        self.index(document_id, pages)
        return document_id

    def index(
        self,
        document_id: str,
        pages: Sequence[str],
        *,
        sections: Sequence[str | None] = (),
        pages_without_text: int = 0,
    ) -> None:
        """Indexes one chunk per page; with `sections`, chunks have sections and no pages. A page
        that is empty stands for a scanned one: it is not indexed and counts as a page without
        text (D7), like `pages_without_text`."""
        file = self._files[document_id]
        file.chunks = [
            _Chunk(
                ref=str(uuid.uuid4()),
                page=None if sections else number,
                section=sections[number - 1] if sections else None,
                text=text,
            )
            for number, text in enumerate(pages, start=1)
            if text
        ]
        without = pages_without_text + sum(1 for text in pages if not text)
        warnings = [DocumentWarning(code="pages_without_text", count=without)] if without else []
        file.status = DocumentStatus(
            status="indexed", pages=None if sections else len(pages), warnings=warnings
        )

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
                collection=file.stored.collection,
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
    """The session's services with `backend` serving documents, whatever backend a capability's
    configuration names: the attachment records stay in Postgres, status and retrieval come
    from `backend`."""
    attachments = services.attachments
    swapped = AttachmentService(
        db=attachments.db,
        store=attachments.store,
        objects=attachments.objects,
        documents=backend,
        limits=attachments.limits,
        status_timeout_s=attachments.status_timeout_s,
        register_response_s=attachments.register_response_s,
    )
    swapped.backends = {other: backend for other in attachments.backends} | {
        getattr(backend, "name", "test"): backend
    }
    name = getattr(backend, "name", "test")
    backends = {other: backend for other in services.backends} | {name: backend}
    # The broker's facade reaches the same backend under every name (ADR-0231).
    access = services.broker.documents
    documents = DocumentAccess(
        backends,
        configured={slug: name for slug in (access.configured if access else {})},
        default=name,
        records=access.records if access else None,
    )
    return dataclasses.replace(
        services,
        documents=backend,
        attachments=swapped,
        backends=backends,
        broker=services.broker.with_documents(documents),
    )
