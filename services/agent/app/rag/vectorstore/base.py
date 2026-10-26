"""VectorStore (README §2, ADR-0131): the only reader and writer of chunks. Every method
names the collection and the owner, so no call reaches another collection's or another
user's chunks. A production store (Milvus, Qdrant) implements this protocol and nothing
else changes."""

from collections.abc import Sequence
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
        conn=None,
    ) -> int:
        """Replaces every chunk of `document_id` with `chunks` (idempotent by document id)."""
        ...

    async def vector_search(
        self,
        owner: Owner,
        collection: str | Sequence[str],
        embedding: Sequence[float],
        *,
        limit: int,
        document_ids: Sequence[str] | None = None,
    ) -> list[ScoredChunk]:
        """The chunks most similar to `embedding` (cosine), best first. Several collections of one
        kind are searched as one (cross-KB retrieval, phase 08); each chunk names its collection."""
        ...

    async def text_search(
        self,
        owner: Owner,
        collection: str | Sequence[str],
        terms: Sequence[str],
        *,
        limit: int,
        document_ids: Sequence[str] | None = None,
    ) -> list[ScoredChunk]:
        """The chunks whose normalized text matches any of `terms`, by full-text rank."""
        ...

    async def purge(self, owner: Owner, collection: str) -> int:
        """Deletes every chunk of the owner's collection (a deleted knowledge base, I1)."""
        ...

    async def fetch(self, owner: Owner, collection: str, ids: Sequence[str]) -> dict[str, str]:
        """The text of each chunk id in the owner's collection; ids outside it are left out."""
        ...
