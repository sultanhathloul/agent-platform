"""Each element of a report spec checked against the passages it cites (owner decision A3 on PR #17,
ADR-0407).

A turn draws from one kind of source (ADR-0398). With knowledge bases or files, a kpi_row, chart or table
may cite only this turn's passages ("P1"…); each of its values counts only when one of those passages holds
it (its canonical figure, or a precise figure's rounding to one or two decimals, ADR-0404) together with a
word of its position and a word of its measure, wherever each has one (ADR-0399's words): a chart point's
category, and its series or the chart's title; a table cell's row label, and its column or the table's
title; a KPI's label (or its row's title). A value not found becomes «—» and is listed as
unavailable under its element in the sources; an element whose values are all unavailable is dropped, and
so is one that cites "general" or nothing of this turn. Figures in text and callouts must be held by some
passage of the turn with a word of their paragraph, else they become «—». Without findings nothing is
matched, every element is general, and its passage ids are dropped.

The result keeps, per document, the elements it supports with their pages or sheets: the page's sources
section is built from it (owner decision B, ADR-0408)."""

import copy
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.artifacts.figures import ARABIC, LATIN, canonical, canonical_number, figures, spans, words
from app.artifacts.spec import Callout, Chart, KpiRow, ReportSpec, Table, Text

MISSING = "—"


@dataclass
class Passage:
    pid: str
    ref: str
    text: str
    document_id: str = ""
    title: str = ""
    publisher: str | None = None
    published_on: str | None = None
    kb_name: dict | None = None
    page: int | None = None
    locator: str | None = None
    _figures: set[str] | None = None
    _words: set[str] | None = None

    @property
    def figures(self) -> set[str]:
        if self._figures is None:
            self._figures = figures([self.text])
        return self._figures

    @property
    def words(self) -> set[str]:
        if self._words is None:
            self._words = words([self.text])
        return self._words

    def holds(self, figure: str, groups: Sequence[Sequence[str]]) -> bool:
        """The passage holds the figure and, for each group of labels that has a word of its own, one of
        them: the value's position (its category or row) and its measure (its series or column, the
        element's title), so 84.3 under «ذكور» in a participation table is not a share of employed men."""
        if figure not in self.figures:
            return False
        for labels in groups:
            wanted = words(labels)
            if not wanted or wanted & self.words:
                continue
            # A label in a script the passage does not use was translated (ADR-0396): the figure decides.
            text = " ".join(labels)
            if (ARABIC.search(text) and ARABIC.search(self.text)) or (LATIN.search(text) and LATIN.search(self.text)):
                return False
        return True


@dataclass
class Use:
    """One element's use of one document: where (pages, sheets) and the values it could not find."""

    kind: str
    title: str
    where: list[str] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)


@dataclass
class Document:
    document_id: str
    title: str
    publisher: str | None
    published_on: str | None
    kb_name: dict | None
    uses: list[Use] = field(default_factory=list)


@dataclass
class Verified:
    spec: ReportSpec
    sourced: bool
    documents: list[Document] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    values: int = 0
    found: int = 0
    unavailable: int = 0
    refs: list[str] = field(default_factory=list)  # the passages a value was found in, in order


class Verifier:
    def __init__(self, passages: Sequence[Passage], *, sourced: bool, kinds: dict[str, str], untitled: str,
                 change: str = "Δ"):
        self.passages = {p.pid: p for p in passages}
        self.sourced = sourced
        self.kinds = kinds  # the sources section's word per element kind
        self.untitled = untitled
        self.change = change  # how a KPI's delta is named among the unavailable values
        self.result = Verified(spec=None, sourced=sourced)  # type: ignore[arg-type]
        self._docs: dict[str, Document] = {}

    # ------------------------------------------------------------------ one element at a time
    def element(self, section):
        """The checked element, or None when it is dropped. The input is never changed."""
        section = copy.deepcopy(section)
        if not self.sourced:
            if hasattr(section, "sources"):
                section.sources = ["general"]
            return section
        if isinstance(section, Text | Callout):
            section.markdown = self._text(section.markdown)
            return section
        cited = [self.passages[s] for s in section.sources if s in self.passages]
        title = getattr(section, "title", None) or self.untitled
        if not cited or "general" in section.sources:
            self.result.dropped.append(title)
            return None
        uses: dict[str, Use] = {}
        missing: list[str] = []
        total = found = 0

        def check(value, groups: list[list[str]], name: str) -> bool:
            nonlocal total, found
            total += 1
            figure = canonical_number(value) if isinstance(value, int | float) else canonical(str(value))
            for passage in cited:
                if passage.holds(figure, groups):
                    found += 1
                    self._use(uses, section, title, passage)
                    if passage.ref not in self.result.refs:
                        self.result.refs.append(passage.ref)
                    return True
            missing.append(name)
            return False

        if isinstance(section, KpiRow):
            for item in section.items:
                groups = [[item.label, section.title or ""]]
                if item.value is not None and not check(item.value, groups, item.label):
                    item.value = None
                if item.delta is not None and not check(item.delta, groups, f"{item.label} ({self.change})"):
                    item.delta = None
        elif isinstance(section, Chart):
            for series in section.series:
                for i, value in enumerate(series.data):
                    if value is None:
                        continue
                    category = section.labels[i] if i < len(section.labels) else ""
                    if not check(value, [[category], [series.name, section.title]], f"{category} · {series.name}"):
                        series.data[i] = None
        elif isinstance(section, Table):
            for row in section.rows:
                for c, cell in enumerate(row):
                    if c == 0 or cell is None:
                        continue  # the row's label is a name, not a value
                    raw = str(cell)
                    found_spans = spans(raw)
                    if not found_spans:
                        continue
                    column = section.columns[c] if c < len(section.columns) else ""
                    groups = [[str(row[0] or "")], [column, section.title]]
                    name = f"{row[0]} · {column}"
                    if not all(check(figure, groups, name) for _, _, figure in found_spans):
                        row[c] = MISSING
        self.result.values += total
        self.result.found += found
        self.result.unavailable += total - found
        # Nothing found, or nothing to find (a chart or KPI row the model left all null): the element leaves.
        if (total and not found) or (not total and not isinstance(section, Table)):
            self.result.dropped.append(title)
            return None
        if missing:
            first = next(iter(uses.values()))
            first.unavailable.extend(dict.fromkeys(missing))
        return section

    def _use(self, uses: dict[str, Use], section, title: str, passage: Passage) -> None:
        doc = self._docs.get(passage.document_id)
        if doc is None:
            doc = Document(passage.document_id, passage.title, passage.publisher, passage.published_on, passage.kb_name)
            self._docs[passage.document_id] = doc
            self.result.documents.append(doc)
        use = uses.get(passage.document_id)
        if use is None:
            use = Use(self.kinds.get(section.kind, ""), title)
            uses[passage.document_id] = use
            doc.uses.append(use)
        where = f"p{passage.page}" if passage.page else (f"s{passage.locator}" if passage.locator else "")
        if where and where not in use.where:
            use.where.append(where)

    def _text(self, markdown: str) -> str:
        """Figures in prose must be held by some passage of the turn with a word of their paragraph."""
        out = []
        for paragraph in re.split(r"(\n\s*\n)", markdown):
            pieces, at = [], 0
            for start, end, figure in spans(paragraph):
                if not any(p.holds(figure, [[paragraph]]) for p in self.passages.values()):
                    pieces.append(paragraph[at:start] + MISSING)
                    at = end
            pieces.append(paragraph[at:])
            out.append("".join(pieces))
        return "".join(out)

    # ------------------------------------------------------------------ the whole spec
    def spec(self, spec: ReportSpec) -> Verified:
        kept = [s for s in (self.element(section) for section in spec.sections) if s is not None]
        self.result.spec = spec.model_copy(update={"sections": kept})
        return self.result
