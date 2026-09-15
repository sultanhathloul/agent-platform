"""Stores kept memory candidates for one (tenant, user) (README §8): each candidate is
embedded, skipped when a stored item of the same scope is at least
`dedupe_similarity` similar, and inserted with its source turn."""

from collections.abc import Sequence

from app.memory.config import MemoryConfig
from app.memory.policy import Candidate
from app.memory.store import MemoryStore
from app.memory.types import MemoryScope
from app.models.embeddings import EmbeddingGateway


class MemoryWriter:
    def __init__(self, store: MemoryStore, embeddings: EmbeddingGateway, config: MemoryConfig):
        self._store = store
        self._embeddings = embeddings
        self._config = config

    async def store_candidates(
        self, scope: MemoryScope, candidates: Sequence[Candidate], *, source_turn: str
    ) -> int:
        stored = 0
        for candidate in candidates:
            vector = await self._embeddings.embed(candidate.text)
            duplicate = await self._store.search(
                scope, vector, limit=1, min_similarity=self._config.dedupe_similarity
            )
            if duplicate:
                continue
            await self._store.add(
                scope,
                kind=candidate.kind,
                text=candidate.text,
                confidence=candidate.confidence,
                embedding=vector,
                source_turn=source_turn,
            )
            stored += 1
        return stored
