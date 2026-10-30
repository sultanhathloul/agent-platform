"""Knowledge bases as the platform runs them (phase 08, ADR-0333 to ADR-0339): the admin operations
(create, list, read, soft delete with the index purged), a KB's documents (registered with the
document backend the kb capability's configuration names, their status, their D3 metadata), and
what a turn may read (the caller's live, permitted KBs; the catalog retrieval and citations use).
Capability-agnostic: no capability is named; the backend comes from the caller.

Rule 12 (docs/RESILIENCE.md): a document's hand-over to its backend answers within
`register_response_s` and goes on in the background, at most one per document, counted toward its
uploader's registrations in flight until it ends (the attachments' limiter, ADR-0265); a status
read is bounded by `status_timeout_s`, past it the last known status is shown and the read ends in
the background, at most one per document (ADR-0245); a purge is bounded, and a purge a backend
could not take is owed and retried when the agent starts."""

import asyncio
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.attachments.limits import UploadLimits
from app.identity import Principal
from app.kb.store import Actor, KbDocumentRecord, KbRecord, KbStore, SourceMetadata
from app.platform_settings import effective
from app.rag.backend import DocumentBackend, DocumentScope, StoredFile
from app.resilience.deadline import detached_context
from app.resilience.limits import Slot

log = logging.getLogger(__name__)

# A purge's bound: one statement on the internal backend, one transport call on an MCP server.
PURGE_TIMEOUT_S = 30.0


class UnsupportedType(Exception):
    pass


class TooManyUploads(Exception):
    pass


class RegisterFailed(Exception):
    """The backend did not take the document; the record as stored after it."""

    def __init__(self, stored: KbDocumentRecord | None):
        super().__init__("register_failed")
        self.stored = stored


@dataclass(frozen=True)
class KbDocumentInfo:
    """What a citation shows of a KB document, and how its versions chain (D3, I4)."""

    document_id: str
    kb: str
    kb_name: Mapping[str, str]
    title: str
    publisher: str
    published_on: str
    source_url: str | None
    supersedes: str | None
    superseded_by: str | None = None
    # The superseding version's title and date, for the evidence's label.
    superseded_by_title: str | None = None
    superseded_by_date: str | None = None


class KbService:
    def __init__(
        self,
        *,
        store: KbStore,
        backend: DocumentBackend,
        backends: Mapping[str, DocumentBackend],
        limits: UploadLimits,
        in_flight=None,
        status_timeout_s: float = 2.0,
        register_response_s: float = 20.0,
    ):
        """`backend`: the one new KB documents are registered with (the kb capability's
        configuration); `backends`: every backend by name, so a document is read from the one that
        took it. `in_flight`: the attachments' registration limiter, shared (ADR-0265)."""
        self.store = store
        self.backend = backend
        self.backends = dict(backends)
        self.limits = limits
        self.in_flight = in_flight
        self.status_timeout_s = status_timeout_s
        self.register_response_s = register_response_s
        self._registrations: dict[str, asyncio.Task[None]] = {}
        self._reads: dict[str, asyncio.Task[KbDocumentRecord]] = {}

    # ------------------------------------------------------------------ what a turn may read

    async def readable(self, principal: Principal) -> list[KbRecord]:
        """The caller's tenant's live KBs it holds `kb:<slug>:read` for (I3, I7)."""
        return [kb for kb in await self.store.list_active(principal.tenant_id) if principal.can_read_kb(kb.slug)]

    async def refused(self, principal: Principal, selected: Iterable[str]) -> list[str]:
        """The selected slugs the caller may not use: no role, another tenant's, deleted, unknown."""
        wanted = list(dict.fromkeys(selected))
        if not wanted:
            return []
        live = await self.store.active_slugs(principal.tenant_id, wanted)
        return [slug for slug in wanted if slug not in live or not principal.can_read_kb(slug)]

    async def catalog(self, tenant_id: str, slugs: Iterable[str]) -> dict[str, KbDocumentInfo]:
        """Every indexed document of the tenant's live KBs among `slugs`, by id, with its KB's names
        and the version that supersedes it, if indexed (D3)."""
        slugs = list(dict.fromkeys(slugs))
        documents = await self.store.indexed_documents(tenant_id, slugs)
        if not documents:
            return {}
        kbs = {kb.slug: kb for kb in await self.store.list_active(tenant_id) if kb.slug in slugs}
        ids = {d.id for d in documents}
        by_id = {d.id: d for d in documents}
        superseded_by = {d.supersedes: d.id for d in documents if d.supersedes in ids}
        return {
            d.id: KbDocumentInfo(
                document_id=d.id,
                kb=d.kb_slug,
                kb_name={"ar": kbs[d.kb_slug].name_ar, "en": kbs[d.kb_slug].name_en},
                title=d.title,
                publisher=d.publisher,
                published_on=d.published_on.isoformat(),
                source_url=d.source_url,
                supersedes=d.supersedes if d.supersedes in ids else None,
                superseded_by=superseded_by.get(d.id),
                superseded_by_title=by_id[superseded_by[d.id]].title if d.id in superseded_by else None,
                superseded_by_date=by_id[superseded_by[d.id]].published_on.isoformat()
                if d.id in superseded_by
                else None,
            )
            for d in documents
            if d.kb_slug in kbs
        }

    # ------------------------------------------------------------------ admin: knowledge bases

    async def delete(self, tenant_id: str, slug: str, actor: Actor) -> tuple[KbRecord, bool] | None:
        """Hides the KB and purges its documents from the index (I1). None when the tenant has no
        live KB by that slug. The second value says whether every backend's index was purged now: a
        backend that failed owes the purge, retried when the agent starts; an MCP backend never
        deletes (D6), its copy is an operations step (ADR-0344). The KB is unreachable either way."""
        kb = await self.store.soft_delete(tenant_id, slug, actor)
        if kb is None:
            return None
        return kb, await self.purge(kb)

    async def purge(self, kb: KbRecord) -> bool:
        names = (await self.store.backends_of(kb.slug)) | {getattr(self.backend, "name", "internal")}
        scope = DocumentScope(tenant_id=kb.tenant_id, user_id=kb.created_by, collections=(kb.collection,))
        purged, owed = True, False
        for name in sorted(names):
            backend = self.backends.get(name)
            if backend is None:
                continue
            try:
                async with asyncio.timeout(PURGE_TIMEOUT_S):
                    removed = await backend.purge(kb.collection, scope)
                log.info("kb %s: %s chunk(s) purged on backend %s", kb.slug, removed, name)
            except NotImplementedError:
                # An MCP server never deletes (D6, ADR-0200): its copy is an operations step (ADR-0344);
                # the KB is unreachable all the same. Not owed: nothing would ever take it.
                purged = False
                log.warning("kb %s: backend %s keeps its copy; removing it is an operations step", kb.slug, name)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                purged = False
                owed = True
                log.warning("kb %s: purge on backend %s failed (%s); owed", kb.slug, name, type(exc).__name__)
        if not owed:
            await self.store.mark_index_purged(kb.slug)
        return purged

    async def settle_owed_purges(self) -> int:
        """The purges a backend could not take when their KB was deleted; at start. Returns how many
        are still owed."""
        for kb in await self.store.owed_purges():
            await self.purge(kb)
        return len(await self.store.owed_purges())

    # ------------------------------------------------------------------ admin: documents

    async def register(
        self,
        kb: KbRecord,
        *,
        name: str,
        mime: str,
        size: int,
        object_key: str,
        metadata: SourceMetadata,
        actor: Actor,
        sha256: str | None = None,
        metadata_missing: bool = False,
    ) -> KbDocumentRecord:
        """Raises UnsupportedType, TooManyUploads or InvalidSupersedes; hands the stored file to the
        backend, which indexes it in the background (I2). Answers within `register_response_s`."""
        kind = self.limits.kind_of(name)
        if kind is None:
            raise UnsupportedType(name)
        slot = self.in_flight.try_acquire(kb.tenant_id, actor.user_id) if self.in_flight is not None else None
        if isinstance(slot, str):
            raise TooManyUploads(slot)
        try:
            record = await self.store.register_document(
                kb,
                name=name,
                mime=mime,
                kind=kind,
                size=size,
                object_key=object_key,
                metadata=metadata,
                backend=getattr(self.backend, "name", "internal"),
                actor=actor,
                sha256=sha256,
                metadata_missing=metadata_missing,
            )
        except BaseException:
            if slot is not None:
                slot.release()
            raise
        handover = self._handover(record, actor, slot)
        response_s = effective.register_response_s(self.register_response_s)
        done, _ = await asyncio.wait([handover], timeout=response_s)
        if handover not in done or handover.cancelled():
            return record
        failure = handover.exception()
        if isinstance(failure, RegisterFailed):
            return failure.stored or record.model_copy(update={"status": "failed", "reason": "register_failed"})
        return record

    def _handover(self, record: KbDocumentRecord, actor: Actor, slot: Slot | None) -> asyncio.Task[None]:
        """The hand-over in flight for this document, or a new one holding `slot` until it ends; it
        belongs to no caller (an upload that stops waiting leaves it running)."""
        running = self._registrations.get(record.id)
        if running is not None:
            if slot is not None:
                slot.release()
            return running
        task = asyncio.get_running_loop().create_task(self._register(record, actor), context=detached_context())
        self._registrations[record.id] = task

        def ended(done: asyncio.Task) -> None:
            self._registrations.pop(record.id, None)
            if slot is not None:
                slot.release()
            if not done.cancelled():
                done.exception()  # retrieved: kept on the record

        task.add_done_callback(ended)
        return task

    async def _register(self, record: KbDocumentRecord, actor: Actor) -> None:
        try:
            await self.backend.register(
                StoredFile(
                    id=record.id,
                    tenant_id=record.tenant_id,
                    user_id=actor.user_id,
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
            log.warning("kb document registration with backend %s failed (%s)", record.backend, type(exc).__name__)
            stored = await self.store.mark_register_failed(record.id) or await self.store.by_id(record.id)
            raise RegisterFailed(stored) from exc

    async def reindex(self, kb: KbRecord, document_id: str, actor: Actor) -> KbDocumentRecord | None:
        """Re-indexes a document an older extractor cut (ADR-0410), or retries a re-index that failed: handed to
        the backend again beside its indexed chunks, which stay in retrieval until the new ones replace them
        (ADR-0424). A document already current is returned unchanged (the seed calls this for every document it
        holds that is due, so a second run does nothing). Counted toward the caller's registrations in flight,
        as an upload is: raises TooManyUploads past the limit."""
        record = await self.store.get_document(kb.tenant_id, kb.slug, document_id)
        if record is None or not record.reindex_due:
            return record
        slot = self.in_flight.try_acquire(kb.tenant_id, actor.user_id) if self.in_flight is not None else None
        if isinstance(slot, str):
            raise TooManyUploads(slot)
        try:
            queued = await self.store.requeue(kb.tenant_id, kb.slug, document_id)
        except BaseException:
            if slot is not None:
                slot.release()
            raise
        if queued is None:
            if slot is not None:
                slot.release()
            return record
        self._handover(queued, actor, slot)
        return queued

    async def documents(self, tenant_id: str, slug: str) -> list[KbDocumentRecord]:
        """A live KB's documents, each with the status its backend reports, under one bound."""
        records = await self.store.list_documents(tenant_id, slug)
        reads = [self._read(r) for r in records if self._asks_backend(r)]
        if reads:
            await asyncio.wait(reads, timeout=self.status_timeout_s)
        shown = []
        for record in records:
            read = self._reads.get(record.id)
            if read is not None and read.done() and not read.cancelled() and read.exception() is None:
                shown.append(read.result())
            else:
                shown.append(record)  # its last known status; the read goes on (ADR-0245)
        return shown

    async def document(self, tenant_id: str, slug: str, document_id: str) -> KbDocumentRecord | None:
        record = await self.store.get_document(tenant_id, slug, document_id)
        if record is None or not self._asks_backend(record):
            return record
        read = self._read(record)
        done, _ = await asyncio.wait([read], timeout=self.status_timeout_s)
        if read in done and not read.cancelled() and read.exception() is None:
            return read.result()
        return record

    def _asks_backend(self, record: KbDocumentRecord) -> bool:
        # The internal backend's record is its status; a failed registration is final (ADR-0254).
        return record.backend != "internal" and record.reason != "register_failed"

    def _read(self, record: KbDocumentRecord) -> asyncio.Task[KbDocumentRecord]:
        read = self._reads.get(record.id)
        if read is None:
            read = asyncio.get_running_loop().create_task(self._status(record), context=detached_context())
            self._reads[record.id] = read

            def ended(done: asyncio.Task) -> None:
                if self._reads.get(record.id) is done:
                    del self._reads[record.id]
                if not done.cancelled() and done.exception() is not None:
                    log.warning("kb document status: backend %s failed (%s)", record.backend,
                                type(done.exception()).__name__)

            read.add_done_callback(ended)
        return read

    async def _status(self, record: KbDocumentRecord) -> KbDocumentRecord:
        backend = self.backends.get(record.backend, self.backend)
        scope = DocumentScope(tenant_id=record.tenant_id, user_id=record.uploaded_by, collections=(record.collection,))
        status = await backend.status(record.id, scope)
        if status is None:
            return record.model_copy(update={"status": "pending", "reason": None, "warnings": []})
        warnings = [w.model_dump() for w in status.warnings]
        await self.store.remember_status(
            record.id, status=status.status, reason=status.reason, pages=status.pages, warnings=warnings
        )
        return record.model_copy(
            update={"status": status.status, "reason": status.reason, "pages": status.pages, "warnings": warnings}
        )
