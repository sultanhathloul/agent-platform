"""The rerank step (owner decisions R2, Q1): a switch; a malformed answer is an error on the
span and the fusion order stands, never all-zero scores; candidates are scored on at most
`max_chars` characters of their text."""

import pytest
from opentelemetry.trace import StatusCode

from app.rag.config import Rerank, Retrieval
from app.rag.rerank import RerankMalformed, parse_scores
from app.rag.retrieve import HybridRetriever
from app.rag.types import Owner, ScoredChunk

OWNER = Owner(tenant_id="t", user_id="u")


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"results": []},
        {"results": [{"index": 0, "relevance_score": 0.9}]},  # one result for two documents
        {"results": [{"index": 0, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.1}]},
        {"results": [{"index": 0, "relevance_score": 0.9}, {"index": 2, "relevance_score": 0.1}]},
        {"results": [{"index": 0, "relevance_score": 0.9}, {"index": 1}]},
        {"results": [{"index": 0, "relevance_score": 0.9}, {"index": 1, "relevance_score": float("nan")}]},
        {"results": [{"index": 0, "relevance_score": "high"}, {"index": 1, "relevance_score": 0.1}]},
        ["not", "an", "object"],
    ],
)
def test_a_malformed_answer_is_refused_never_read_as_zeros(body):
    with pytest.raises(RerankMalformed):
        parse_scores(body, 2)


def test_a_well_formed_answer_gives_one_score_per_document_in_order():
    body = {"results": [{"index": 1, "relevance_score": 0.2}, {"index": 0, "relevance_score": 0.7}]}
    assert parse_scores(body, 2) == [0.7, 0.2]


def _chunk(id_: str, ord_: int, text: str) -> ScoredChunk:
    return ScoredChunk(id=id_, attachment_id="a", ord=ord_, text=text, score=0.0)


class _Store:
    """Vector order a, b, c; text order c, b: fused c (1/63 + 1/61), b (2/62), a (1/61)."""

    def __init__(self, chunks):
        self.chunks = chunks

    async def vector_search(self, owner, collection, embedding, *, limit, document_ids=None):
        return [self.chunks[i].model_copy(update={"vector_rank": r}) for r, i in enumerate((0, 1, 2), 1)]

    async def text_search(self, owner, collection, terms, *, limit, document_ids=None):
        return [self.chunks[i].model_copy(update={"text_rank": r}) for r, i in enumerate((2, 1), 1)]


class _Embeddings:
    async def embed(self, text):
        return [1.0]


class _Reranker:
    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.seen = answer, error, []

    async def rerank(self, query, documents):
        self.seen.append(list(documents))
        if self.error:
            raise self.error
        return self.answer(documents)


def _retriever(reranker, **rerank) -> HybridRetriever:
    config = Retrieval(
        vector_top_k=30,
        text_top_k=30,
        rrf_k=60,
        rerank=Rerank(**{"enabled": True, "candidates": 3, "max_chars": None, **rerank}),
        top_k=2,
    )
    chunks = [_chunk("a", 0, "أ" * 50), _chunk("b", 1, "ب" * 50), _chunk("c", 2, "ج" * 50)]
    return HybridRetriever(_Store(chunks), _Embeddings(), reranker, config, counter=None)


async def test_the_reranker_orders_the_candidates():
    reranker = _Reranker(answer=lambda docs: [0.1, 0.2, 0.9][: len(docs)])  # fused c, b, a: a best
    top = await _retriever(reranker).retrieve(OWNER, "sess_x", "سؤال")
    assert [c.id for c in top] == ["a", "b"]


async def test_switched_off_the_top_k_is_the_fusion_order_and_nothing_is_scored():
    reranker = _Reranker(answer=lambda docs: [1.0] * len(docs))
    top = await _retriever(reranker, enabled=False).retrieve(OWNER, "sess_x", "سؤال")
    assert [c.id for c in top] == ["c", "b"] and reranker.seen == []


async def test_a_malformed_answer_flags_the_span_and_keeps_the_fusion_order(spans):
    reranker = _Reranker(error=RerankMalformed("no results"))
    top = await _retriever(reranker).retrieve(OWNER, "sess_x", "سؤال")
    assert [c.id for c in top] == ["c", "b"]
    assert all(c.score > 0 for c in top)  # fusion scores, not zeros
    [span] = [s for s in spans.get_finished_spans() if s.name == "rag.retrieve"]
    assert span.status.status_code == StatusCode.ERROR
    assert span.attributes["rag.rerank.error"] == "RerankMalformed"


async def test_candidates_are_scored_on_at_most_max_chars_and_are_returned_whole():
    reranker = _Reranker(answer=lambda docs: [0.3, 0.2, 0.1][: len(docs)])
    top = await _retriever(reranker, candidates=2, max_chars=10).retrieve(OWNER, "sess_x", "سؤال")
    [scored] = reranker.seen
    assert len(scored) == 2 and all(len(d) == 10 for d in scored)
    assert all(len(c.text) == 50 for c in top)
