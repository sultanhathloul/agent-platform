"""Page attribution after the answer (ADR-0140, owner decision C1): deterministic, no model
call, capability-agnostic. Two steps (ADR-0182, ADR-0183):

1. Which chunks may be cited. The question's own words are dropped from the answer first: a
   chunk is eligible only when it shares at least `min_shared_ngrams` word n-grams (`ngram`
   words, normalized and light-stemmed by app/text/arabic.py) with the answer that are not made
   only of the question's words, and holds at least `min_new_word_coverage` of the words of one
   answer sentence that are not in the question (per sentence, so an answer drawing on several
   chunks, one fact each, still cites each). An answer that restates the question ("لا يذكر الملف
   مدة إجازة الزواج") or adds only its own phrasing ("لا توجد معلومات في السيرة الذاتية")
   therefore cites nothing.
2. Which eligible chunks are cited. Each is scored by its best sentence (a line, or a span
   ending in . ! ? or ؟): the answer's n-grams that sentence holds, each weighted by
   log((N + 1) / n), where N is the number of retrieved chunks and n the number holding the
   n-gram. Boilerplate every chunk repeats counts for almost nothing, and the words that single
   a line out count most; scoring by sentence keeps a neighbouring page that holds the same
   numbers on different lines below the page whose one line holds them together. A chunk is
   cited when it scores at least `relative_to_best` of the best.

When no chunk passes, there is no citation, so a general-knowledge answer or "not in the
document" shows no source. The rule is config/citations.yaml.

The words naming the sources themselves (title, publisher, KB name, date) are dropped with the
question's (phase 08, ADR-0354): a KB answer states its source and date in the sentence (D3), and
those words are in a document's metadata, not its chunk text. A claim ends at a clause mark too
(، ؛ ;), so one sentence joining two facts from two sources cites both. An answer in another writing
system than a chunk (English over Arabic) is attributed on the numbers it states, outside the
question, the sources' names and publication years and the dates it writes (ADR-0361). Number words
are folded to digits on both sides first (ADR-0359)."""

import math
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.text.arabic import fold_number_words, search_terms
from app.text.script import script_of

MAX_SECTIONS = 3
# A claim of the answer ends at a sentence end or a clause mark: one sentence may join two facts from
# two sources with an Arabic comma (phase 08, ADR-0354); never at a decimal point, «3.6» stays whole (ADR-0361).
_CLAUSE_END = re.compile(r"(?:[\n!?؟،;؛]|\.(?!\d)|(?<!\d)\.)+")
_NUMBER = re.compile(r"(?<![\d.])\d+(?:\.\d+)?(?![\d])")
_INDIC = str.maketrans({**{a: str(i) for i, a in enumerate("٠١٢٣٤٥٦٧٨٩")}, **{a: str(i) for i, a in enumerate("۰۱۲۳۴۵۶۷۸۹")}, "٫": "."})
# What a citation copies from its source when the source has it: a knowledge base document's KB,
# its KB's name, the publisher, the publication date and the source URL (phase 08, D3, I4).
SOURCE_METADATA = ("kb", "kb_name", "publisher", "published_on", "url")
_SENTENCE_END = re.compile(r"[\n.!?؟]+")


class AttributionRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ngram: int = Field(ge=1)
    min_shared_ngrams: int = Field(ge=1)
    min_new_word_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    relative_to_best: float = Field(default=0.0, ge=0.0, le=1.0)
    # Words that introduce a source ("وفقًا لـ", "الصادر عن", "بتاريخ"): dropped with the sources' names
    # (ADR-0358).
    source_phrasing: list[str] = Field(default_factory=list)


def load_attribution_rule(config_dir: Path) -> AttributionRule:
    raw = yaml.safe_load((config_dir / "citations.yaml").read_text(encoding="utf-8"))
    return AttributionRule.model_validate(raw["attribution"])


def _naming(source: dict[str, Any]) -> str:
    """The words that name a source: its title, publisher, KB name in either language, and date."""
    names = source.get("kb_name") or {}
    parts = [source.get("title"), source.get("publisher"), source.get("published_on")]
    if isinstance(names, dict):
        parts += [names.get("ar"), names.get("en")]
    return " ".join(str(p) for p in parts if p)


_MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|november|december"
    "|يناير|فبراير|مارس|أبريل|ابريل|إبريل|مايو|يونيو|يوليو|أغسطس|اغسطس|سبتمبر|أكتوبر|اكتوبر|نوفمبر|ديسمبر"
)
# A date the answer writes when it names its source (D3): its day and month are no figures (ADR-0361).
_DATE = re.compile(
    rf"\d{{4}}-\d{{1,2}}-\d{{1,2}}|\d{{1,2}}/\d{{1,2}}/\d{{2,4}}"
    rf"|(?:{_MONTHS})\s+\d{{1,2}}(?!\d)(?:st|nd|rd|th)?(?:,?\s*\d{{4}})?"
    rf"|(?<!\d)\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:{_MONTHS})(?:,?\s*\d{{4}})?",
    re.IGNORECASE,
)


def _figures(text: str) -> set[str]:
    """The numbers a text states outside the dates it writes."""
    return _numbers(_DATE.sub(" ", (text or "").translate(_INDIC)))


def _named_numbers(source: dict[str, Any]) -> set[str]:
    """The numbers naming a source: those in its title, publisher and KB name, and its publication year;
    never its date's day and month, which are figures elsewhere (ADR-0361)."""
    names = source.get("kb_name") or {}
    parts = [source.get("title"), source.get("publisher")]
    if isinstance(names, dict):
        parts += [names.get("ar"), names.get("en")]
    numbers = _numbers(fold_number_words(" ".join(str(p) for p in parts if p)))
    if source.get("published_on"):
        numbers.add(str(source["published_on"])[:4])
    return numbers


def _numbers(text: str) -> set[str]:
    """The numbers a text holds, Arabic-Indic digits and the Arabic decimal mark folded."""
    return set(_NUMBER.findall((text or "").translate(_INDIC)))


def _script(text: str) -> str | None:
    """The writing system most of a text's letters are in (ARABIC, LATIN, ...), or None."""
    counts: dict[str, int] = {}
    for char in text or "":
        if char.isalpha():
            script = script_of(char)
            if script:
                counts[script] = counts.get(script, 0) + 1
    return max(counts, key=counts.__getitem__) if counts else None


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
    {id, title, source, document_id, pages, locator}, plus the source's SOURCE_METADATA it has. `texts` holds each chunk's text by its
    reference; a source without a document or a text is never cited. `locator` names
    sections when the document has no pages (DOCX, XLSX, TXT, MD)."""
    # Number words are digits on both sides (ADR-0359): «واحد وعشرين» in the answer and «واحد وعشرون»
    # in the chunk are both 21, and an English answer's "ninety" meets the chunk's «تسعين».
    answer, question = fold_number_words(answer or ""), fold_number_words(question or "")
    asked = set(search_terms(question))
    # Words that name a source (its title, publisher, KB name and date) say where a fact comes from,
    # not what it is: an answer that states its source and date (D3, phase 08) is attributed on its
    # facts, as the question's own words are dropped (ADR-0354).
    asked |= {term for source in sources for term in search_terms(fold_number_words(_naming(source)))}
    asked |= set(search_terms(" ".join(rule.source_phrasing)))
    terms = search_terms(answer or "")
    grams = ngrams(terms, rule.ngram)
    new_grams = {gram for gram in grams if not set(gram) <= asked}
    claims = [
        new
        for sentence in _CLAUSE_END.split(answer or "")
        if (new := {term for term in search_terms(sentence) if term not in asked})
    ]
    # An answer in another writing system than a chunk (an English answer over Arabic documents)
    # shares no words with it; the numbers it states, outside the question and the sources' names
    # and dates, decide instead (ADR-0354).
    answer_script = _script(answer)
    named_numbers = _figures(question) | {n for source in sources for n in _named_numbers(source)}
    answer_numbers = _figures(answer) - named_numbers
    if not new_grams and not answer_numbers:
        return []
    shared: list[set[tuple[str, ...]]] = []
    lines: list[list[set[tuple[str, ...]]]] = []
    eligible: list[bool] = []
    for source in sources:
        if not (source.get("document_id") and source.get("ref") in texts):
            shared.append(set())
            lines.append([])
            eligible.append(False)
            continue
        text = fold_number_words(texts[source["ref"]])
        chunk_script = _script(text)
        if answer_script and chunk_script and answer_script != chunk_script:
            common = {(number,) for number in answer_numbers & _numbers(text)}
            shared.append(common)
            # By line, never at a decimal point: "3.6" stays one number.
            lines.append([{(n,) for n in answer_numbers & _numbers(line)} for line in text.splitlines()])
            eligible.append(bool(common))
            continue
        chunk_terms = search_terms(text)
        common = grams & ngrams(chunk_terms, rule.ngram)
        shared.append(common)
        lines.append([grams & ngrams(search_terms(line), rule.ngram) for line in _SENTENCE_END.split(text)])
        eligible.append(
            len(common & new_grams) >= rule.min_shared_ngrams
            and any(len(new & set(chunk_terms)) >= rule.min_new_word_coverage * len(new) for new in claims)
        )
    retrieved = sum(1 for source in sources if source.get("ref") in texts)
    spread: dict[tuple[str, ...], int] = {}
    for common in shared:
        for gram in common:
            spread[gram] = spread.get(gram, 0) + 1
    weight = {gram: math.log((retrieved + 1) / held) for gram, held in spread.items()}
    scores = [
        max((sum(weight[gram] for gram in line) for line in chunk_lines), default=0.0) if ok else 0.0
        for chunk_lines, ok in zip(lines, eligible, strict=True)
    ]
    floor = rule.relative_to_best * max(scores, default=0.0)
    chosen = [score > 0 and score >= floor for score in scores]
    # A claim whose best chunk lies in a document not cited yet cites that document by its own floor:
    # an answer joining facts from two documents (two knowledge bases) cites both, while an answer
    # drawing on one document keeps the single floor above (phase 08, ADR-0354).
    # Claims are visited strongest first, so the claim that singles a document out most decides its
    # pages: a clause holding only boilerplate ("في عام 2030") ties neighbouring pages and must not
    # cite them before the clause holding the figure does (ADR-0357).
    documents = {s["document_id"] for s, keep in zip(sources, chosen, strict=True) if keep}
    per_claim = []
    for clause in _CLAUSE_END.split(answer or ""):
        claim = {g for g in ngrams(search_terms(clause), rule.ngram) if g in new_grams}
        claim |= {(n,) for n in _figures(clause) - named_numbers}
        per_claim.append([
            max((sum(weight.get(g, 0.0) for g in line & claim) for line in chunk_lines), default=0.0) if ok else 0.0
            for chunk_lines, ok in zip(lines, eligible, strict=True)
        ])
    for claim_scores in sorted(per_claim, key=lambda scores: max(scores, default=0.0), reverse=True):
        best = max(claim_scores, default=0.0)
        if best <= 0:
            continue
        document = sources[claim_scores.index(best)]["document_id"]
        if document in documents:
            continue
        documents.add(document)
        for index, (source, score) in enumerate(zip(sources, claim_scores, strict=True)):
            if source.get("document_id") == document and score > 0 and score >= rule.relative_to_best * best:
                chosen[index] = True
    cited: dict[str, dict] = {}
    for source, keep in zip(sources, chosen, strict=True):
        if not keep:
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
                # A source's own metadata, when it has some (a KB document's, phase 08, I4).
                **{key: source[key] for key in SOURCE_METADATA if source.get(key) is not None},
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
