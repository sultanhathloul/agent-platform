"""Page attribution after the answer (ADR-0140): deterministic, no model call. A
retrieved chunk is cited when it shares at least `min_shared_ngrams` word n-grams
(`ngram` words, normalized and light-stemmed by app/text/arabic.py) with the answer; its
page joins its document's citation. When no chunk passes, there is no citation, so a
general-knowledge answer or "not in the document" shows no source."""

from collections.abc import Sequence
from typing import Any

from app.text.arabic import search_terms

MAX_SECTIONS = 3


def ngrams(text: str, n: int) -> set[tuple[str, ...]]:
    terms = search_terms(text)
    return {tuple(terms[i : i + n]) for i in range(len(terms) - n + 1)}


def attribute(answer: str, sources: Sequence[dict[str, Any]], *, ngram: int, min_shared: int) -> list[dict]:
    """Citations, one per document in the order its first cited chunk was retrieved:
    {id, title, source, document_id, pages, locator}; `locator` names sections when the
    document has no pages (DOCX, XLSX, TXT, MD)."""
    wanted = ngrams(answer or "", ngram)
    if not wanted:
        return []
    cited: dict[str, dict] = {}
    for source in sources:
        document, excerpt = source.get("document_id"), source.get("excerpt")
        if not document or not excerpt:
            continue
        if len(wanted & ngrams(excerpt, ngram)) < min_shared:
            continue
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
