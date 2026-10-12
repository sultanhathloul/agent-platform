"""VectorStore (README §2, ADR-0131): the only reader and writer of chunks. Every method
names the collection and the owner, so no call reaches another collection's or another
user's chunks. A production store (Milvus, Qdrant) implements this protocol and nothing
else changes."""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from app.rag.types import Chunk, Owner, ScoredChunk


class VectorStore(Protocol):
    async def replace_document(
        self,
        owner: Owner,
        collection: str,
        document_id: str,
        chunks: Sequence[Chunk],
        embeddings: Sequence[Sequence[float]],
        *,
        expires_at: datetime | None,
        conn=None,
    ) -> int:
        """Replaces every chunk of `document_id` with `chunks` (idempotent by document id)."""
        ...

    async def vector_search(
        self,
        owner: Owner,
        collection: str,
        embedding: Sequence[float],
        *,
        limit: int,
        document_ids: Sequence[str] | None = None,
    ) -> list[ScoredChunk]:
        """The chunks most similar to `embedding` (cosine), best first."""
        ...

    async def text_search(
        self,
        owner: Owner,
        collection: str,
        terms: Sequence[str],
        *,
        limit: int,
        document_ids: Sequence[str] | None = None,
    ) -> list[ScoredChunk]:
        """The chunks whose normalized text matches any of `terms`, by full-text rank."""
        ...

    async def fetch(self, owner: Owner, collection: str, ids: Sequence[str]) -> dict[str, str]:
        """The text of each chunk id in the owner's collection; ids outside it are left out."""
        ...

    async def delete_document(self, owner: Owner, collection: str, document_id: str, *, conn=None) -> int: ...

    async def touch(self, owner: Owner, collection: str, expires_at: datetime, *, conn=None) -> int:
        """Moves the expiry of the owner's chunks in the collection (D1)."""
        ...

    async def purge_document(self, document_id: str, *, conn=None) -> int:
        """Deletes a document's chunks whoever owns them: the retention purge only."""
        ...
