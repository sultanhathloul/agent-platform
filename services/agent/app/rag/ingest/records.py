"""The records the ingest service works on (phase 08, I2): attachments by their id, KB documents by
`kb:<id>` (app/rag/ingest/queue.py). One adapter with the attachment store's methods, so the
pipeline, the worker and the orphan sweeper run unchanged on both: each call goes to the store
the item names. A KB's chunks are owned by its tenant with no user; the vector store keeps them
by KB document (app/rag/vectorstore/pgvector.py)."""

from typing import Any

from app.attachments.store import AttachmentStore
from app.rag.ingest.queue import KB_ITEM
from app.rag.types import FailureReason


class IngestRecords:
    def __init__(self, attachments: AttachmentStore, kbs=None):
        """`kbs`: the KB store (app/kb/store.py), or None on a platform without knowledge bases."""
        self.attachments = attachments
        self.kbs = kbs

    def _store(self, item: str):
        if item.startswith(KB_ITEM):
            if self.kbs is None:
                return None, item[len(KB_ITEM) :]
            return self.kbs, item[len(KB_ITEM) :]
        return self.attachments, item

    async def start_attempt(self, item: str):
        store, key = self._store(item)
        return await store.start_attempt(key) if store is not None else None

    async def by_id(self, item: str):
        store, key = self._store(item)
        return await store.by_id(key) if store is not None else None

    async def mark_indexed(
        self, item: str, *, pages: int | None, chunks: int, conn, warnings: list[dict[str, Any]] | None = None
    ) -> bool:
        store, key = self._store(item)
        if store is None:
            return False
        return await store.mark_indexed(key, pages=pages, chunks=chunks, conn=conn, warnings=warnings)

    async def mark_failed(self, item: str, reason: FailureReason, *, pages: int | None = None) -> bool:
        store, key = self._store(item)
        return await store.mark_failed(key, reason, pages=pages) if store is not None else False

    async def pending_ids(self) -> list[str]:
        """Every pending item of the internal backend: attachments, then KB documents."""
        items = await self.attachments.pending_ids()
        if self.kbs is not None:
            items += [f"{KB_ITEM}{key}" for key in await self.kbs.pending_ids()]
        return items

    async def known_keys(self, keys: list[str]) -> set[str]:
        """The object keys among `keys` that an attachment or a KB document names (the sweeper)."""
        known = await self.attachments.known_keys(keys)
        if self.kbs is not None:
            known |= await self.kbs.known_keys([k for k in keys if k not in known])
        return known
