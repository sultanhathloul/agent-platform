"""Retrieval's pure parts (owner decision A3, G4): reciprocal rank fusion and the full-text
query's stopwords, on Arabic questions."""

import pytest

from app.rag.config import load_docs_config
from app.rag.retrieve import fuse, query_terms
from app.rag.types import ScoredChunk


@pytest.fixture(scope="module")
def stopwords(config_dir) -> list[str]:
    """The configured ones (config/docs.yaml retrieval.stopwords), as retrieval uses them."""
    configured = load_docs_config(config_dir).retrieval.stopwords
    return [*configured.get("ar", []), *configured.get("en", [])]


def _chunk(id_: str, ord_: int = 0, *, vector_rank=None, text_rank=None, attachment="a") -> ScoredChunk:
    return ScoredChunk(
        id=id_,
        attachment_id=attachment,
        ord=ord_,
        text=id_,
        score=0.0,
        vector_rank=vector_rank,
        text_rank=text_rank,
    )


def test_fusion_adds_one_over_k_plus_rank_per_list():
    by_vector = [_chunk("x", vector_rank=1), _chunk("y", 1, vector_rank=2)]
    by_text = [_chunk("y", 1, text_rank=1), _chunk("z", 2, text_rank=2)]
    fused = fuse([by_vector, by_text], k=60)
    assert [c.id for c in fused] == ["y", "x", "z"]  # in both lists beats first in one
    scores = {c.id: c.score for c in fused}
    assert scores["y"] == 1 / 62 + 1 / 61
    assert scores["x"] == 1 / 61 and scores["z"] == 1 / 62
    y = fused[0]
    assert (y.vector_rank, y.text_rank) == (2, 1)  # both ranks are kept


def test_fusion_breaks_ties_by_document_then_position():
    fused = fuse([[_chunk("b2", 2, attachment="b")], [_chunk("a5", 5, attachment="a")]], k=60)
    assert [c.id for c in fused] == ["a5", "b2"]


def test_the_query_keeps_content_words_once_and_drops_stopwords_after_normalization(stopwords):
    terms = query_terms("ما مدة إجازة الأمومة؟ وهل تُمنح الإجازة براتب كامل حسب اللائحة؟", stopwords)
    assert "ما" not in terms and "هل" not in terms and "حسب" not in terms
    assert terms.count("اجازه") == 1  # إجازة and الإجازة: one normalized stem
    assert {"مده", "امومه", "راتب", "لائحه"} <= set(terms)


def test_english_stopwords_and_single_letters_are_dropped(stopwords):
    assert query_terms("What does the document say about leave in 2031 a", stopwords) == [
        "about",
        "leave",
        "2031",
    ]


def test_with_floor_keeps_each_collections_best_chunks_in_ranked_order():
    from app.rag.retrieve import with_floor
    from app.rag.types import ScoredChunk

    def chunk(i, collection):
        return ScoredChunk(id=f"c{i}", attachment_id="d", ord=i, text="t", score=1.0 - i / 100, collection=collection)

    ranked = [chunk(i, "kb_a") for i in range(6)] + [chunk(6, "kb_b"), chunk(7, "kb_b"), chunk(8, "kb_b")]
    assert [c.id for c in with_floor(ranked, 4, 0)] == ["c0", "c1", "c2", "c3"]
    assert [c.id for c in with_floor(ranked, 4, 2)] == ["c0", "c1", "c6", "c7"]
    assert [c.id for c in with_floor(ranked, 8, 2)] == ["c0", "c1", "c2", "c3", "c4", "c5", "c6", "c7"]
    assert [c.id for c in with_floor(ranked[:3], 8, 2)] == ["c0", "c1", "c2"]  # fewer than k: all
