"""Attachments of a conversation (phase 04, A1–A3): registration with the file limit, the
hand-over to the document backend, and owner-scoped reads with the status the backend
reports. The agent indexes nothing itself: the ingest service does (owner decision R4).
Capability-agnostic. The product deletes no file (owner decision S4): files stay for as long
as the platform keeps them, and deleting one is an operations task.

An upload answers within `register_response_s` whatever the backend does (I5, ADR-0254): the
hand-over runs as work of its own, at most one per attachment, and past that bound the upload
answers with the file `pending` while the hand-over goes on in the background, within the
backend's own bounds (an MCP server's semaphore and timeout_s). Its outcome is kept on the record:
a failure ends the file `failed` with `register_failed`, which is final."""

import asyncio
import logging
from collections.abc import Mapping

from app.attachments.limits import UploadLimits
from app.attachments.objects import ObjectStore
from app.attachments.store import AttachmentRecord, AttachmentStore
from app.db import Database
from app.rag.backend import DocumentBackend, DocumentScope, StoredFile
from app.rag.types import Owner
from app.resilience.deadline import detached_context
from app.resilience.limits import Slot
from app.tools.broker import auditing

log = logging.getLogger(__name__)
# The bound on one read of a conversation's statuses when the configuration names none.
DEFAULT_STATUS_TIMEOUT_S = 2.0
# How long an upload waits for its backend to take the file when the configuration names none.
DEFAULT_REGISTER_RESPONSE_S = 20.0


class TooManyUploads(Exception):
    """The user's, its tenant's or the platform's registrations in flight are at their limit
    (429 too_many_uploads, ADR-0265)."""


class _RegisterFailed(Exception):
    """A hand-over the backend did not take, with the record as stored after it (ADR-0254, ADR-0265)."""

    def __init__(self, stored: AttachmentRecord | None):
        super().__init__("register_failed")
        self.stored = stored


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
        register_response_s: float = DEFAULT_REGISTER_RESPONSE_S,
    ):
        """`documents`: the backend new uploads are registered with; `backends`: every backend by
        name, so an upload's status is read from the one it was registered with (I11).
        `status_timeout_s`: one bound for reading all of a conversation's statuses (ADR-0233).
        `register_response_s`: how long an upload waits for its backend to take the file (ADR-0254)."""
        self.db = db
        self.store = store
        self.objects = objects
        self.documents = documents
        self.limits = limits
        self.backends = dict(backends or {})
        self.status_timeout_s = status_timeout_s
        self.register_response_s = register_response_s
        # Status reads in flight, by attachment id: at most one each; a later read joins it (ADR-0245).
        self._reads: dict[str, asyncio.Task[AttachmentRecord]] = {}
        # Hand-overs to a backend in flight, by attachment id: at most one each (ADR-0254).
        self._registrations: dict[str, asyncio.Task[None]] = {}
        # Each counts toward its user's uploads in flight until it ends (ADR-0265); none without
        # limits (a service that only reads statuses).
        self.in_flight = limits.registrations() if limits is not None else None

    async def register(
        self, owner: Owner, conversation_id: str, *, name: str, mime: str, size: int, object_key: str
    ) -> AttachmentRecord:
        """Raises UnsupportedType, TooManyUploads or LimitReached; hands the stored file to the
        document backend, which indexes it in the background (C6). Answers within
        `register_response_s`: the record `pending` while the hand-over goes on; when it failed in
        that time, the record as stored after it (`failed` with `register_failed`, or what the
        backend made of it meanwhile, ADR-0265). The registration counts toward the user's uploads
        in flight until it ends, here or in the background."""
        kind = self.limits.kind_of(name)
        if kind is None:
            raise UnsupportedType(name)
        slot = self.in_flight.try_acquire(owner.tenant_id, owner.user_id)
        if isinstance(slot, str):
            raise TooManyUploads(slot)
        try:
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
        except BaseException:
            slot.release()
            raise
        handover = self._register(record, slot)
        done, _ = await asyncio.wait([handover], timeout=self.register_response_s)
        if handover not in done:
            log.warning(
                "attachment registration past %.1f s: answered pending, it goes on in the background",
                self.register_response_s,
            )
            return record
        if handover.cancelled():
            return record
        failure = handover.exception()
        if isinstance(failure, _RegisterFailed):
            failed = record.model_copy(update={"status": "failed", "reason": "register_failed"})
            return failure.stored or failed
        return record

    def _register(self, record: AttachmentRecord, slot: Slot) -> asyncio.Task[None]:
        """The hand-over of `record` in flight, or a new one, which holds `slot` until it ends. It
        belongs to no caller: it runs in a context without the caller's deadline, and an upload
        that stops waiting leaves it running."""
        handover = self._registrations.get(record.id)
        if handover is not None:
            slot.release()  # joined: the hand-over in flight holds its own
            return handover
        handover = asyncio.get_running_loop().create_task(self._handover(record), context=detached_context())
        self._registrations[record.id] = handover

        def ended(done: asyncio.Task) -> None:
            self._registrations.pop(record.id, None)
            slot.release()
            if not done.cancelled():
                done.exception()  # retrieved: a background failure is logged and kept on the record

        handover.add_done_callback(ended)
        return handover

    async def _handover(self, record: AttachmentRecord) -> None:
        """Hands the stored file to the backend; a failure ends the record `register_failed`."""
        try:
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
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning(
                "attachment registration with backend %s failed (%s)", record.backend, type(exc).__name__
            )
            stored = await self.store.mark_register_failed(record.id)
            if stored is None:
                # No row matched: the backend moved the record on meanwhile (the internal worker
                # indexed what the push queued before the call failed); its state is the answer.
                stored = await self.store.by_id(record.id)
            raise _RegisterFailed(stored) from exc

    async def with_status(
        self, owner: Owner, conversation_id: str, *, on_audit=None
    ) -> list[AttachmentRecord]:
        """The owner's attachments of the conversation, each with the status its document
        backend reports (C6): the record says what was uploaded, the backend how far indexing
        went. A record the backend does not know reads `pending`. The statuses are read at once,
        under one bound for all of them; a backend that does not answer in time, or cannot (its
        server down, its breaker open), leaves the record's last known status (ADR-0233), so a
        status read never stalls a turn. A read past the bound is not cancelled: it ends in the
        background, within its server's semaphore and timeout, and keeps what it read on the
        record; a later read of the same attachment joins it (ADR-0245). `on_audit`: where a
        transport call's audit entry goes inside a turn."""
        scope = _scope(owner, conversation_id)
        records = await self.store.list_for(owner, conversation_id)
        if not records:
            return []
        with auditing("attachments", on_audit):
            reads = [self._read(record, scope) for record in records]
        done, pending = await asyncio.wait(reads, timeout=self.status_timeout_s)
        if pending:
            log.warning(
                "attachment status: %d read(s) past %.1f s; last known status shown",
                len(pending),
                self.status_timeout_s,
            )
        shown = []
        for record, read in zip(records, reads, strict=True):
            if read in done and not read.cancelled() and read.exception() is None:
                shown.append(read.result())
            else:
                shown.append(record)  # its failure, if any, is logged by `_read_ended`
        return shown

    async def get_with_status(self, owner: Owner, conversation_id: str, attachment_id: str):
        record = await self.store.get(owner, conversation_id, attachment_id)
        if record is None:
            return None
        read = self._read(record, _scope(owner, conversation_id))
        done, _ = await asyncio.wait([read], timeout=self.status_timeout_s)
        if read in done and not read.cancelled() and read.exception() is None:
            return read.result()
        return record  # the last known status; the read goes on (ADR-0245)

    def _read(self, record: AttachmentRecord, scope: DocumentScope) -> asyncio.Task[AttachmentRecord]:
        """The status read of `record` in flight, or a new one. It belongs to no caller: a caller
        that stops waiting (its bound, its own cancellation) leaves it running."""
        read = self._reads.get(record.id)
        if read is None:
            # Without the caller's deadline: a turn that ends must not cut a read it does not own.
            read = asyncio.get_running_loop().create_task(
                self._with_status(record, scope), context=detached_context()
            )
            self._reads[record.id] = read
            read.add_done_callback(lambda done: self._read_ended(record, done))
        return read

    def _read_ended(self, record: AttachmentRecord, read: asyncio.Task) -> None:
        if self._reads.get(record.id) is read:
            del self._reads[record.id]
        if not read.cancelled() and read.exception() is not None:
            log.warning(
                "attachment status: backend %s failed (%s)", record.backend, type(read.exception()).__name__
            )

    async def _with_status(self, record: AttachmentRecord, scope: DocumentScope) -> AttachmentRecord:
        if record.reason == "register_failed":
            return record  # final: the backend is not asked (ADR-0254)
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
