"""Memory similarity against Postgres with hand-built vectors (README §8, ADR-0058):
the dedupe threshold, the retrieval floor, ranking, and the dimension filter. The
mock LiteLLM returns one fixed embedding, so these paths need vectors chosen here."""

import math
import uuid

import pytest

from app.memory.policy import Candidate
from app.memory.retriever import MemoryRetriever
from app.memory.store import MemoryStore
from app.memory.types import MemoryScope
from app.memory.writer import MemoryWriter

pytestmark = pytest.mark.integration


def _scope() -> MemoryScope:
    return MemoryScope(tenant_id="default", user_id=f"u-{uuid.uuid4().hex[:8]}")


def _at(similarity: float) -> list[float]:
    """A unit vector whose cosine similarity with [1, 0, 0] is `similarity`."""
    return [similarity, math.sqrt(1 - similarity**2), 0.0]


QUERY = [1.0, 0.0, 0.0]


class _Embeddings:
    def __init__(self, vectors: dict[str, list[float]]):
        self.vectors = vectors

    async def embed(self, text: str) -> list[float]:
        return self.vectors[text]


async def _add(store: MemoryStore, scope: MemoryScope, text: str, vector: list[float]) -> str:
    return await store.add(
        scope, kind="preference", text=text, confidence=0.9, embedding=vector, source_turn="t"
    )


async def test_search_ranks_by_similarity_and_applies_the_floor_and_the_limit(services):
    store, scope = services.memory.store, _scope()
    ids = {s: await _add(store, scope, f"item {s}", _at(s)) for s in (0.95, 0.7, 0.55, 0.45)}

    hits = await store.search(scope, QUERY, limit=5, min_similarity=0.5)
    assert [h.id for h in hits] == [ids[0.95], ids[0.7], ids[0.55]]  # 0.45 is under the floor
    assert [round(h.similarity, 3) for h in hits] == [0.95, 0.7, 0.55]
    assert [h.id for h in await store.search(scope, QUERY, limit=2, min_similarity=0.5)] == [
        ids[0.95],
        ids[0.7],
    ]
    assert await store.search(scope, QUERY, limit=5, min_similarity=0.96) == []


async def test_search_compares_only_vectors_of_the_query_dimension(services):
    store, scope = services.memory.store, _scope()
    same = await _add(store, scope, "three dimensions", [1.0, 0.0, 0.0])
    await _add(store, scope, "four dimensions", [1.0, 0.0, 0.0, 0.0])
    await _add(store, scope, "two dimensions", [1.0, 0.0])

    assert [h.id for h in await store.search(scope, QUERY, limit=5, min_similarity=0.0)] == [same]
    four = await store.search(scope, [1.0, 0.0, 0.0, 0.0], limit=5, min_similarity=0.0)
    assert [h.text for h in four] == ["four dimensions"]


async def test_the_retriever_uses_min_similarity_from_config(services):
    config = services.memory.config
    store, scope = services.memory.store, _scope()
    above = await _add(store, scope, "above", _at(config.min_similarity + 0.05))
    await _add(store, scope, "below", _at(config.min_similarity - 0.05))
    retriever = MemoryRetriever(store, _Embeddings({"question": QUERY}), config)
    assert [h.id for h in await retriever.retrieve(scope, "question", 5)] == [above]


async def test_the_writer_skips_a_candidate_at_or_above_dedupe_similarity(services):
    config = services.memory.config
    threshold = config.dedupe_similarity
    store, scope = services.memory.store, _scope()
    await _add(store, scope, "stored", QUERY)
    vectors = {
        "near duplicate": _at(threshold + 0.01),
        "related but new": _at(threshold - 0.02),
    }
    writer = MemoryWriter(store, _Embeddings(vectors), config)
    candidates = [Candidate(kind="preference", text=text, confidence=0.9) for text in vectors]

    assert await writer.store_candidates(scope, candidates, source_turn="t2") == 1
    assert sorted(i.text for i in await store.list_items(scope)) == ["related but new", "stored"]
