"""Page attribution after the answer (ADR-0140, owner decision C1): deterministic, no model
call, capability-agnostic. Two steps (ADR-0182):

1. Which chunks may be cited. The question's own words are dropped from the answer first: a
   chunk is eligible only when it shares at least `min_shared_ngrams` word n-grams (`ngram`
   words, normalized and light-stemmed by app/text/arabic.py) with the answer that are not made
   only of the question's words, and holds at least `min_new_word_coverage` of the answer's
   words that are not in the question. An answer that restates the question ("لا يذكر الملف
   مدة إجازة الزواج") or adds only its own phrasing ("لا توجد معلومات في السيرة الذاتية")
   therefore cites nothing.
2. Which eligible chunks are cited. Each is scored by the answer's n-grams it shares, each
   n-gram weighted by one over the number of retrieved chunks that hold it, so boilerplate
   every page repeats counts for little and the words that single a page out count most; a
   chunk is cited when it scores at least `relative_to_best` of the best.

When no chunk passes, there is no citation, so a general-knowledge answer or "not in the
document" shows no source. The rule is config/citations.yaml."""

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.text.arabic import search_terms

MAX_SECTIONS = 3


class AttributionRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ngram: int = Field(ge=1)
    min_shared_ngrams: int = Field(ge=1)
    min_new_word_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    relative_to_best: float = Field(default=0.0, ge=0.0, le=1.0)


def load_attribution_rule(config_dir: Path) -> AttributionRule:
    raw = yaml.safe_load((config_dir / "citations.yaml").read_text(encoding="utf-8"))
    return AttributionRule.model_validate(raw["attribution"])


def ngrams(terms: Sequence[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(terms[i : i + n]) for i in range(len(terms) - n + 1)}


def attribute(
    answer: str,
    sources: Sequence[dict[str, Any]],
    texts: Mapping[str, str],
    rule: AttributionRule,
    *,
    question: str = "",
) -> list[dict]:
    """Citations, one per document in the order its first cited chunk was retrieved:
    {id, title, source, document_id, pages, locator}. `texts` holds each chunk's text by its
    reference; a source without a document or a text is never cited. `locator` names
    sections when the document has no pages (DOCX, XLSX, TXT, MD)."""
    asked = set(search_terms(question or ""))
    terms = search_terms(answer or "")
    grams = ngrams(terms, rule.ngram)
    new_grams = {gram for gram in grams if not set(gram) <= asked}
    new_words = {term for term in terms if term not in asked}
    if not new_grams:
        return []
    shared: list[set[tuple[str, ...]]] = []
    eligible: list[bool] = []
    for source in sources:
        if not (source.get("document_id") and source.get("ref") in texts):
            shared.append(set())
            eligible.append(False)
            continue
        chunk_terms = search_terms(texts[source["ref"]])
        common = grams & ngrams(chunk_terms, rule.ngram)
        shared.append(common)
        eligible.append(
            len(common & new_grams) >= rule.min_shared_ngrams
            and len(new_words & set(chunk_terms)) >= rule.min_new_word_coverage * len(new_words)
        )
    spread: dict[tuple[str, ...], int] = {}
    for common in shared:
        for gram in common:
            spread[gram] = spread.get(gram, 0) + 1
    scores = [
        sum(1 / spread[gram] for gram in common) if ok else 0.0
        for common, ok in zip(shared, eligible, strict=True)
    ]
    floor = rule.relative_to_best * max(scores, default=0.0)
    cited: dict[str, dict] = {}
    for source, score in zip(sources, scores, strict=True):
        if score <= 0 or score < floor:
            continue
        document = source["document_id"]
        entry = cited.setdefault(
            document,
            {
                "id": document,
                "title": source.get("title") or "",
                "source": source.get("kind") or "doc",
                "document_id": document,
                "pages": set(),
                "sections": [],
            },
        )
        if source.get("page"):
            entry["pages"].add(int(source["page"]))
        elif source.get("locator") and source["locator"] not in entry["sections"]:
            entry["sections"].append(source["locator"])
    citations = []
    for entry in cited.values():
        sections = entry.pop("sections")
        entry["pages"] = sorted(entry["pages"])
        entry["locator"] = " · ".join(sections[:MAX_SECTIONS]) if sections and not entry["pages"] else None
        citations.append(entry)
    return citations
