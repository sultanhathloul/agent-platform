"""Attachments of a conversation (phase 04, A1–A4): registration with the file limit,
ingestion on the queue, owner-scoped reads and deletes, the expiry a turn moves forward
(D1) and the daily purge. Capability-agnostic: the `docs` capability reads what this
indexes through the retrieval library."""

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta

from app.attachments.objects import ObjectStore, StorageUnavailable
from app.attachments.store import AttachmentRecord, AttachmentStore, collection_for
from app.db import Database
from app.observability.otel import tracer
from app.rag.backend import DocumentBackend, DocumentScope, StoredFile
from app.rag.config import DocsConfig
from app.rag.ingest.worker import IngestWorker
from app.rag.types import Owner
from app.rag.vectorstore.base import VectorStore

log = logging.getLogger(__name__)


class UnsupportedType(Exception):
    pass


class AttachmentService:
    def __init__(
        self,
        *,
        db: Database,
        store: AttachmentStore,
        objects: ObjectStore,
        vectors: VectorStore,
        documents: DocumentBackend,
        worker: IngestWorker | None,
        config: DocsConfig,
    ):
        self.db = db
        self.store = store
        self.objects = objects
        self.vectors = vectors
        self.documents = documents
        self.worker = worker
        self.config = config
        self._purge_task: asyncio.Task | None = None

    async def register(
        self, owner: Owner, conversation_id: str, *, name: str, mime: str, size: int, object_key: str
    ) -> AttachmentRecord:
        """Raises UnsupportedType or LimitReached; hands the stored file to the document
        backend, which indexes it in the background (C6)."""
        kind = self.config.kind_of(name)
        if kind is None:
            raise UnsupportedType(name)
        record = await self.store.register(
            owner,
            conversation_id,
            name=name,
            mime=mime,
            kind=kind,
            size=size,
            object_key=object_key,
            max_files=self.config.limits.max_files_per_conversation,
            retention_days=self.config.retention_days,
        )
        await self.documents.register(
            StoredFile(
                id=record.id,
                conversation_id=record.conversation_id,
                tenant_id=record.tenant_id,
                user_id=record.user_id,
                name=record.name,
                mime=record.mime,
                kind=record.kind,
                size=record.size,
                object_key=record.object_key,
            )
        )
        return record

    async def with_status(self, owner: Owner, conversation_id: str) -> list[AttachmentRecord]:
        """The owner's attachments of the conversation, each with the status its document
        backend reports (C6): the record says what was uploaded, the backend how far indexing
        went. A record the backend does not know reads `pending`."""
        scope = DocumentScope(
            tenant_id=owner.tenant_id, user_id=owner.user_id, conversation_id=conversation_id
        )
        records = await self.store.list_for(owner, conversation_id)
        return [await self._with_status(record, scope) for record in records]

    async def get_with_status(self, owner: Owner, conversation_id: str, attachment_id: str):
        record = await self.store.get(owner, conversation_id, attachment_id)
        if record is None:
            return None
        scope = DocumentScope(
            tenant_id=owner.tenant_id, user_id=owner.user_id, conversation_id=conversation_id
        )
        return await self._with_status(record, scope)

    async def _with_status(self, record: AttachmentRecord, scope: DocumentScope) -> AttachmentRecord:
        status = await self.documents.status(record.id, scope)
        if status is None:
            return record.model_copy(update={"status": "pending", "reason": None})
        return record.model_copy(
            update={"status": status.status, "reason": status.reason, "pages": status.pages}
        )

    async def delete(self, owner: Owner, conversation_id: str, attachment_id: str) -> bool:
        """The record and its chunks go in one transaction, then the object (D1 purge
        catches an object a storage outage left behind)."""
        record = await self.store.get(owner, conversation_id, attachment_id)
        if record is None:
            return False
        async with self.db.connection() as conn, conn.transaction():
            await self.vectors.delete_document(owner, record.collection, record.id, conn=conn)
            if not await self.store.delete(owner, conversation_id, attachment_id, conn=conn):
                return False
        try:
            await self.objects.delete(record.object_key)
        except StorageUnavailable:
            log.warning("object of attachment %s not deleted: storage unavailable", attachment_id)
        return True

    async def touch(self, owner: Owner, conversation_id: str) -> None:
        """A turn of the conversation moves the expiry of its files and chunks (D1)."""
        expires_at = datetime.now(UTC) + timedelta(days=self.config.retention_days)
        async with self.db.connection() as conn, conn.transaction():
            if await self.store.touch(owner, conversation_id, expires_at, conn=conn):
                await self.vectors.touch(owner, collection_for(conversation_id), expires_at, conn=conn)

    async def purge_expired(self) -> dict[str, int]:
        """Deletes every expired attachment: its object, its chunks, its record (D1).
        Idempotent; a step that fails leaves the record for the next run."""
        counts = {"attachments": 0, "chunks": 0, "objects": 0, "errors": 0}
        with tracer.start_as_current_span("attachments.purge") as span:
            for record in await self.store.expired():
                try:
                    await self.objects.delete(record.object_key)
                    counts["objects"] += 1
                    async with self.db.connection() as conn, conn.transaction():
                        counts["chunks"] += await self.vectors.purge_document(record.id, conn=conn)
                        counts["attachments"] += await self.store.delete_by_id(record.id, conn=conn)
                except StorageUnavailable:
                    counts["errors"] += 1
            for key, value in counts.items():
                span.set_attribute(f"purge.{key}", value)
        return counts

    async def start(self) -> None:
        if self.worker is not None:
            await self.worker.start()
        self._purge_task = asyncio.create_task(self._purge_loop())

    async def stop(self) -> None:
        if self._purge_task is not None:
            self._purge_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._purge_task
        if self.worker is not None:
            await self.worker.stop()

    async def _purge_loop(self) -> None:
        while True:
            try:
                counts = await self.purge_expired()
                if counts["attachments"] or counts["errors"]:
                    log.info("attachment purge: %s", counts)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("attachment purge failed: %s", type(exc).__name__)
            await asyncio.sleep(self.config.purge.interval_hours * 3600)
