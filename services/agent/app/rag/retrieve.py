"""Hybrid retrieval (README §7 step 6, §9.3; ADR-0133): the top `vector_top_k` chunks by
vector similarity and the top `text_top_k` by full-text rank over Arabic-normalized
text, fused by reciprocal rank fusion, the best `rerank_candidates` reranked, the top
`top_k` kept. Every query names the collection and the owner (ADR-0131). The
`rag.retrieve` span carries ids, pages, scores and counts, never chunk text (ADR-0137)."""

import hashlib
import json
from collections.abc import Sequence

from app.models.embeddings import EmbeddingGateway
from app.observability.otel import tracer
from app.rag.config import Retrieval
from app.rag.rerank import Reranker
from app.rag.types import Owner, ScoredChunk
from app.rag.vectorstore.base import VectorStore
from app.text.arabic import search_stem, search_terms


def section_hash(section: str | None) -> str | None:
    return hashlib.sha256(section.encode("utf-8")).hexdigest()[:16] if section else None


def query_terms(question: str, stopwords: Sequence[str]) -> list[str]:
    """The question's search stems without stopwords, in order, once each."""
    stop = {search_stem(word) for word in stopwords}
    seen: list[str] = []
    for term in search_terms(question):
        if term not in stop and term not in seen and len(term) > 1:
            seen.append(term)
    return seen


def fuse(rankings: Sequence[Sequence[ScoredChunk]], k: int) -> list[ScoredChunk]:
    """Reciprocal rank fusion: each list contributes 1 / (k + rank) to a chunk's score."""
    fused: dict[str, ScoredChunk] = {}
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, chunk in enumerate(ranking, start=1):
            scores[chunk.id] = scores.get(chunk.id, 0.0) + 1.0 / (k + rank)
            known = fused.get(chunk.id)
            fused[chunk.id] = (
                chunk
                if known is None
                else known.model_copy(
                    update={
                        "vector_rank": known.vector_rank or chunk.vector_rank,
                        "text_rank": known.text_rank or chunk.text_rank,
                    }
                )
            )
    ordered = sorted(fused.values(), key=lambda c: (-scores[c.id], c.attachment_id, c.ord))
    return [c.model_copy(update={"score": scores[c.id]}) for c in ordered]


class HybridRetriever:
    def __init__(
        self, store: VectorStore, embeddings: EmbeddingGateway, reranker: Reranker, config: Retrieval
    ):
        self._store = store
        self._embeddings = embeddings
        self._reranker = reranker
        self._config = config

    async def retrieve(
        self,
        owner: Owner,
        collection: str,
        question: str,
        *,
        document_ids: Sequence[str] | None = None,
        locale: str = "ar",
    ) -> list[ScoredChunk]:
        config = self._config
        with tracer.start_as_current_span("rag.retrieve") as span:
            span.set_attribute("rag.collection", collection)
            embedding = await self._embeddings.embed(question)
            stopwords = [*config.stopwords.get("ar", []), *config.stopwords.get("en", [])]
            by_vector = await self._store.vector_search(
                owner, collection, embedding, limit=config.vector_top_k, document_ids=document_ids
            )
            by_text = await self._store.text_search(
                owner,
                collection,
                query_terms(question, stopwords),
                limit=config.text_top_k,
                document_ids=document_ids,
            )
            candidates = fuse([by_vector, by_text], config.rrf_k)[: config.rerank_candidates]
            scores = await self._reranker.rerank(question, [c.text for c in candidates])
            ranked = sorted(
                (c.model_copy(update={"score": s}) for c, s in zip(candidates, scores, strict=True)),
                key=lambda c: (-c.score, c.attachment_id, c.ord),
            )
            top = ranked[: config.top_k]
            span.set_attribute("rag.vector_hits", len(by_vector))
            span.set_attribute("rag.text_hits", len(by_text))
            span.set_attribute("rag.candidates", len(candidates))
            span.set_attribute("rag.returned", len(top))
            span.set_attribute(
                "rag.results",
                json.dumps(
                    [
                        {
                            "chunk_id": c.id,
                            "attachment_id": c.attachment_id,
                            "page": c.page,
                            # A hash, never the heading itself: the eval matches its expected
                            # section without document text in the trace (ADR-0137).
                            "section_sha": section_hash(c.section),
                            "score": round(c.score, 4),
                            "vector_rank": c.vector_rank,
                            "text_rank": c.text_rank,
                        }
                        for c in top
                    ]
                ),
            )
            return top
