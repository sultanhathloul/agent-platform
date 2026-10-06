"""Memory retrieval (README §7 step 5, §8): the top-k items of one (tenant, user) by
embedding similarity to the message. A user whose memory is off gets nothing. An
embedding failure degrades to no memory for that call, recorded on the span,
rather than failing the turn (ADR-0058)."""

import logging

from opentelemetry import trace

from app.memory.config import MemoryConfig
from app.memory.store import MemoryStore
from app.memory.types import MemoryHit, MemoryScope
from app.models.embeddings import EmbeddingGateway
from app.models.gateway import ModelUnavailable

log = logging.getLogger(__name__)


class MemoryRetriever:
    def __init__(self, store: MemoryStore, embeddings: EmbeddingGateway, config: MemoryConfig):
        self._store = store
        self._embeddings = embeddings
        self._config = config

    async def retrieve(self, scope: MemoryScope, query: str, top_k: int) -> list[MemoryHit]:
        span = trace.get_current_span()
        if top_k <= 0 or not query.strip():
            return []
        if not await self._store.enabled(scope):
            span.set_attribute("memory.enabled", False)
            return []
        try:
            vector = await self._embeddings.embed(query)
        except ModelUnavailable as exc:
            span.add_event("memory.retrieve_failed", {"error": type(exc).__name__})
            log.warning("memory retrieval skipped: %s", exc)
            return []
        hits = await self._store.search(
            scope, vector, limit=top_k, min_similarity=self._config.min_similarity
        )
        span.set_attribute("memory.hits", len(hits))
        return hits
