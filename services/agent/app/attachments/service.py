"""Attachments of a conversation (phase 04, A1–A3): registration with the file limit, the
hand-over to the document backend, and owner-scoped reads with the status the backend
reports. The agent indexes nothing itself: the ingest service does (owner decision R4).
Capability-agnostic. The product deletes no file (owner decision S4): files stay for as long
as the platform keeps them, and deleting one is an operations task."""

from app.attachments.limits import UploadLimits
from app.attachments.objects import ObjectStore
from app.attachments.store import AttachmentRecord, AttachmentStore
from app.db import Database
from app.rag.backend import DocumentBackend, DocumentScope, StoredFile
from app.rag.types import Owner


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
    ):
        self.db = db
        self.store = store
        self.objects = objects
        self.documents = documents
        self.limits = limits

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

    async def with_status(self, owner: Owner, conversation_id: str) -> list[AttachmentRecord]:
        """The owner's attachments of the conversation, each with the status its document
        backend reports (C6): the record says what was uploaded, the backend how far indexing
        went. A record the backend does not know reads `pending`."""
        scope = _scope(owner, conversation_id)
        records = await self.store.list_for(owner, conversation_id)
        return [await self._with_status(record, scope) for record in records]

    async def get_with_status(self, owner: Owner, conversation_id: str, attachment_id: str):
        record = await self.store.get(owner, conversation_id, attachment_id)
        if record is None:
            return None
        return await self._with_status(record, _scope(owner, conversation_id))

    async def _with_status(self, record: AttachmentRecord, scope: DocumentScope) -> AttachmentRecord:
        status = await self.documents.status(record.id, scope)
        if status is None:
            return record.model_copy(update={"status": "pending", "reason": None, "warnings": []})
        return record.model_copy(
            update={
                "status": status.status,
                "reason": status.reason,
                "pages": status.pages,
                "warnings": [w.model_dump() for w in status.warnings],
            }
        )


def _scope(owner: Owner, conversation_id: str) -> DocumentScope:
    return DocumentScope.session(
        tenant_id=owner.tenant_id, user_id=owner.user_id, conversation_id=conversation_id
    )
