"""Page attribution after the answer (ADR-0140, owner decision C1): deterministic, no model
call, capability-agnostic. A retrieved chunk is cited when it shares at least
`min_shared_ngrams` word n-grams (`ngram` words, normalized and light-stemmed by
app/text/arabic.py) with the answer, and at least `relative_to_best` of what the best-matching
chunk shares; its page joins its document's citation. The question's own words are dropped
from the answer before its n-grams are taken, so an answer that only restates the question
("لا يذكر الملف مدة إجازة الزواج") shares nothing with the chunk the question matched. When
no chunk passes, there is no citation, so a general-knowledge answer or "not in the document"
shows no source. The rule is config/citations.yaml."""

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
    relative_to_best: float = Field(default=0.0, ge=0.0, le=1.0)


def load_attribution_rule(config_dir: Path) -> AttributionRule:
    raw = yaml.safe_load((config_dir / "citations.yaml").read_text(encoding="utf-8"))
    return AttributionRule.model_validate(raw["attribution"])


def ngrams(terms: Sequence[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(terms[i : i + n]) for i in range(len(terms) - n + 1)}


def answer_terms(answer: str, question: str = "") -> list[str]:
    """The answer's search terms, in order, without the question's own words."""
    asked = set(search_terms(question or ""))
    return [term for term in search_terms(answer or "") if term not in asked]


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
    wanted = ngrams(answer_terms(answer, question), rule.ngram)
    if not wanted:
        return []
    shared = [
        len(wanted & ngrams(search_terms(texts[s["ref"]]), rule.ngram))
        if s.get("document_id") and s.get("ref") in texts
        else 0
        for s in sources
    ]
    floor = max(rule.min_shared_ngrams, rule.relative_to_best * max(shared, default=0))
    cited: dict[str, dict] = {}
    for source, count in zip(sources, shared, strict=True):
        if count < floor or count == 0:
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
