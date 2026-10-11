"""One ingestion job (README §9.3, ADR-0130): read the upload, detect its type, extract,
chunk, embed and write the chunks, then mark the attachment `indexed`; or mark it
`failed` with a reason code. Idempotent by attachment id: the chunks are replaced and
the status set in one transaction, so a job run twice, or resumed after a crash, leaves
one copy. A transient failure (storage, embeddings) raises `Retry`; the worker retries
it until `ingest.max_attempts` and then marks it failed. The `rag.ingest` span carries
the attachment id, kind, pages, chunk count, outcome and reason, never document text
(ADR-0137)."""

import asyncio
import logging
import time

from opentelemetry import trace

from app.attachments.objects import ObjectStore, StorageUnavailable
from app.attachments.store import AttachmentStore
from app.context.tokens import TokenCounter
from app.db import Database
from app.models.embeddings import EmbeddingGateway
from app.models.gateway import ModelUnavailable
from app.observability.otel import tracer
from app.rag.chunk import chunk_segments
from app.rag.config import DocsConfig
from app.rag.extract import ExtractionFailed, detect_kind, extract
from app.rag.types import FailureReason
from app.rag.vectorstore.base import VectorStore

log = logging.getLogger(__name__)


class Retry(Exception):
    """A transient failure; the job may succeed later."""

    def __init__(self, reason: FailureReason):
        super().__init__(reason)
        self.reason = reason


class IngestPipeline:
    def __init__(
        self,
        *,
        db: Database,
        attachments: AttachmentStore,
        objects: ObjectStore,
        vectors: VectorStore,
        embeddings: EmbeddingGateway,
        config: DocsConfig,
        counter: TokenCounter,
    ):
        self._db = db
        self._attachments = attachments
        self._objects = objects
        self._vectors = vectors
        self._embeddings = embeddings
        self._config = config
        self._counter = counter

    async def run(self, attachment_id: str) -> str:
        """`indexed`, `failed`, or `skipped` (gone, or no longer pending). Raises Retry."""
        with tracer.start_as_current_span("rag.ingest") as span:
            span.set_attribute("attachment.id", attachment_id)
            started = time.monotonic()
            record = await self._attachments.start_attempt(attachment_id)
            if record is None:
                span.set_attribute("ingest.outcome", "skipped")
                return "skipped"
            span.set_attribute("attachment.kind", record.kind)
            span.set_attribute("ingest.attempt", record.attempts)
            try:
                outcome = await self._index(record, span)
            except ExtractionFailed as failure:
                await self._attachments.mark_failed(attachment_id, failure.reason, pages=failure.pages)
                span.set_attribute("ingest.reason", failure.reason)
                outcome = "failed"
            except _Abandoned:
                outcome = "skipped"
            span.set_attribute("ingest.outcome", outcome)
            span.set_attribute("ingest.ms", int((time.monotonic() - started) * 1000))
            return outcome

    async def fail(self, attachment_id: str, reason: FailureReason) -> None:
        await self._attachments.mark_failed(attachment_id, reason)

    async def _index(self, record, span: trace.Span) -> str:
        config = self._config
        try:
            data = await self._objects.get(record.object_key)
        except StorageUnavailable as exc:
            raise Retry("storage_unavailable") from exc
        if len(data) > config.limits.max_file_bytes:
            raise ExtractionFailed("unsupported_type", detail="over the size limit")
        kind = detect_kind(record.name, data, config)
        if kind is None or kind != record.kind:
            raise ExtractionFailed("unsupported_type")
        document = await asyncio.to_thread(extract, kind, data, config)
        chunks = chunk_segments(
            document.segments,
            self._counter,
            max_tokens=config.chunking.max_tokens,
            overlap_tokens=config.chunking.overlap_tokens,
        )
        if not chunks:
            raise ExtractionFailed("empty_document", pages=document.pages)
        vectors: list[list[float]] = []
        batch = config.ingest.embed_batch
        try:
            for start in range(0, len(chunks), batch):
                vectors.extend(
                    await self._embeddings.embed_many([c.text for c in chunks[start : start + batch]])
                )
        except ModelUnavailable as exc:
            raise Retry("embedding_unavailable") from exc
        span.set_attribute("ingest.pages", document.pages or 0)
        span.set_attribute("ingest.chunks", len(chunks))
        async with self._db.connection() as conn, conn.transaction():
            await self._vectors.replace_document(
                record.owner,
                record.collection,
                record.id,
                chunks,
                vectors,
                expires_at=record.expires_at,
                conn=conn,
            )
            if not await self._attachments.mark_indexed(
                record.id, pages=document.pages, chunks=len(chunks), conn=conn
            ):
                # Deleted or finished elsewhere while this job ran: keep nothing it wrote.
                raise _Abandoned
        return "indexed"


class _Abandoned(Exception):
    pass
