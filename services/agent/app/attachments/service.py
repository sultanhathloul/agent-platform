"""Attachments of a conversation (phase 04, A1–A3): registration with the file limit, the
hand-over to the document backend, and owner-scoped reads with the status the backend
reports. The agent indexes nothing itself: the ingest service does (owner decision R4).
Capability-agnostic. The product deletes no file (owner decision S4): files stay for as long
as the platform keeps them, and deleting one is an operations task."""

import asyncio
import logging
from collections.abc import Mapping

from app.attachments.limits import UploadLimits
from app.attachments.objects import ObjectStore
from app.attachments.store import AttachmentRecord, AttachmentStore
from app.db import Database
from app.rag.backend import DocumentBackend, DocumentScope, StoredFile
from app.rag.types import Owner
from app.tools.broker import auditing

log = logging.getLogger(__name__)
# The bound on one read of a conversation's statuses when the configuration names none.
DEFAULT_STATUS_TIMEOUT_S = 2.0


class UnsupportedType(Exception):
    pass


class AttachmentService:
    def __init__(
        self,
        *,
        db: Database,
        store: AttachmentStore,
        objects: ObjectStore,
        documents: DocumentBackend,
        limits: UploadLimits,
        backends: Mapping[str, DocumentBackend] | None = None,
        status_timeout_s: float = DEFAULT_STATUS_TIMEOUT_S,
    ):
        """`documents`: the backend new uploads are registered with; `backends`: every backend by
        name, so an upload's status is read from the one it was registered with (I11).
        `status_timeout_s`: one bound for reading all of a conversation's statuses (ADR-0233)."""
        self.db = db
        self.store = store
        self.objects = objects
        self.documents = documents
        self.limits = limits
        self.backends = dict(backends or {})
        self.status_timeout_s = status_timeout_s

    async def register(
        self, owner: Owner, conversation_id: str, *, name: str, mime: str, size: int, object_key: str
    ) -> AttachmentRecord:
        """Raises UnsupportedType or LimitReached; hands the stored file to the document
        backend, which indexes it in the background (C6)."""
        kind = self.limits.kind_of(name)
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
            max_files=self.limits.max_files_per_conversation,
            backend=getattr(self.documents, "name", "internal"),
        )
        await self.documents.register(
            StoredFile(
                id=record.id,
                conversation_id=record.conversation_id,
                tenant_id=record.tenant_id,
                user_id=record.user_id,
                collection=record.collection,
                name=record.name,
                mime=record.mime,
                kind=record.kind,
                size=record.size,
                object_key=record.object_key,
            )
        )
        return record

    async def with_status(
        self, owner: Owner, conversation_id: str, *, on_audit=None
    ) -> list[AttachmentRecord]:
        """The owner's attachments of the conversation, each with the status its document
        backend reports (C6): the record says what was uploaded, the backend how far indexing
        went. A record the backend does not know reads `pending`. The statuses are read at once,
        under one bound for all of them; a backend that does not answer in time, or cannot (its
        server down, its breaker open), leaves the record's last known status (ADR-0233), so a
        status read never stalls a turn. `on_audit`: where a transport call's audit entry goes
        inside a turn."""
        scope = _scope(owner, conversation_id)
        records = await self.store.list_for(owner, conversation_id)
        if not records:
            return []
        with auditing("attachments", on_audit):
            tasks = [asyncio.ensure_future(self._with_status(record, scope)) for record in records]
        done, pending = await asyncio.wait(tasks, timeout=self.status_timeout_s)
        for task in pending:
            task.cancel()
        if pending:
            log.warning(
                "attachment status: %d read(s) past %.1f s; last known status shown",
                len(pending),
                self.status_timeout_s,
            )
            await asyncio.gather(*pending, return_exceptions=True)
        shown = []
        for record, task in zip(records, tasks, strict=True):
            if task in done and not task.cancelled() and task.exception() is None:
                shown.append(task.result())
            else:
                if task in done and not task.cancelled() and task.exception() is not None:
                    log.warning(
                        "attachment status: backend %s failed (%s)",
                        record.backend,
                        type(task.exception()).__name__,
                    )
                shown.append(record)
        return shown

    async def get_with_status(self, owner: Owner, conversation_id: str, attachment_id: str):
        record = await self.store.get(owner, conversation_id, attachment_id)
        if record is None:
            return None
        try:
            async with asyncio.timeout(self.status_timeout_s):
                return await self._with_status(record, _scope(owner, conversation_id))
        except Exception as exc:  # noqa: BLE001 (any failure shows the last known status)
            log.warning("attachment status: backend %s failed (%s)", record.backend, type(exc).__name__)
            return record

    async def _with_status(self, record: AttachmentRecord, scope: DocumentScope) -> AttachmentRecord:
        backend = self.backends.get(record.backend, self.documents)
        status = await backend.status(record.id, scope)
        if status is None:
            return record.model_copy(update={"status": "pending", "reason": None, "warnings": []})
        warnings = [w.model_dump() for w in status.warnings]
        if record.backend != "internal":
            await self.store.remember_status(
                record.owner,
                record.id,
                status=status.status,
                reason=status.reason,
                pages=status.pages,
                warnings=warnings,
            )
        return record.model_copy(
            update={
                "status": status.status,
                "reason": status.reason,
                "pages": status.pages,
                "warnings": warnings,
            }
        )


def _scope(owner: Owner, conversation_id: str) -> DocumentScope:
    return DocumentScope.session(
        tenant_id=owner.tenant_id, user_id=owner.user_id, conversation_id=conversation_id
    )
