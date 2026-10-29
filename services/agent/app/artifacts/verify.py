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
from app.text.arabic import normalize

MISSING = "—"


# ------------------------------------------------------------------ qualifiers (owner decision 1 on PR #17, ADR-0414)

_AMBIGUOUS = "*"
_NON_SAUDI = re.compile(r"غير\s*(?:ال)?سعودي|non[- ]?saudi", re.I)
_SAUDI = re.compile(r"سعودي|سعوديات|saudi", re.I)
_SAUDI_WOMEN = re.compile(r"سعوديات")
_MALE = re.compile(r"(?<![\w])(?:وال|بال|لل|ال|ل|و)?(?:ذكور|ذكر|رجال)(?![\w])|\b(?:males?|men)\b", re.I)
_FEMALE = re.compile(r"(?<![\w])(?:وال|بال|لل|ال|ل|و)?(?:اناث|انثي|نساء|بينهن)(?![\w])|سعوديات|\b(?:females?|women)\b", re.I)
_QUARTERS = {"الاول": 1, "الثاني": 2, "الثالث": 3, "الرابع": 4}
_QUARTER = re.compile(r"الربع\s*(الاول|الثاني|الثالث|الرابع)|\bQ([1-4])\b", re.I)
_YEAR = re.compile(r"(?<!\d)(20\d\d)(?!\d)")
_TOTAL = re.compile(r"اجمالي|الاجمالي|لاجمالي|\btotal\b|\boverall\b", re.I)
_BOTH = re.compile(
    r"(?:لل|ال)?سعوديين\s*و\s*غير\s*(?:ال)?سعوديين|(?:ال)?ذكور\s*و\s*(?:ال)?اناث|(?:ال)?اناث\s*و\s*(?:ال)?ذكور"
)
# How far before a figure prose's qualifiers are read (ADR-0414), back to the last full stop at most.
PROSE_WINDOW = 400
_FULL_STOP = re.compile(r"[.!?؟]\s+(?=[^\W\d_])")


@dataclass(frozen=True)
class Qualifiers:
    """What a label or a row says of its group: sex, nationality (None: the total; "*": both named, which
    prose cannot settle), and its period."""

    sex: str | None = None
    nationality: str | None = None
    quarter: int | None = None
    year: str | None = None

    @classmethod
    def of(cls, text: str) -> "Qualifiers":
        folded = normalize(text)
        rest = _NON_SAUDI.sub(" ", folded)
        non_saudi, saudi = bool(_NON_SAUDI.search(folded)), bool(_SAUDI.search(rest))
        nationality = _AMBIGUOUS if non_saudi and saudi else "non_saudi" if non_saudi else "saudi" if saudi else None
        male, female = bool(_MALE.search(folded)), bool(_FEMALE.search(folded))
        sex = _AMBIGUOUS if male and female else "male" if male else "female" if female else None
        quarter = _QUARTER.search(folded)
        years = set(_YEAR.findall(folded))
        return cls(
            sex=sex,
            nationality=nationality,
            quarter=(_QUARTERS.get(quarter.group(1)) if quarter.group(1) else int(quarter.group(2))) if quarter else None,
            year=next(iter(years)) if len(years) == 1 else None,
        )

    @classmethod
    def before(cls, text: str) -> "Qualifiers":
        """The qualifiers prose gives a figure: in the text before it, the nearest sex and nationality named
        win («… السعوديات … 30.5 … أما الذكور السعوديون … 63.9»: 63.9 is the men's), a «الإجمالي» after them
        makes it the total, and a pair named together («للسعوديين وغير السعوديين»، «الذكور والإناث») is the
        total too."""
        folded = _BOTH.sub(" اجمالي ", normalize(text))

        def last(*patterns: re.Pattern) -> list[int]:
            return [max((m.end() for m in p.finditer(folded)), default=-1) for p in patterns]

        total = max((m.end() for m in _TOTAL.finditer(folded)), default=-1)
        rest = _NON_SAUDI.sub(lambda m: " " * len(m.group(0)), folded)
        non_saudi = max((m.end() for m in _NON_SAUDI.finditer(folded)), default=-1)
        saudi = max((m.end() for m in _SAUDI.finditer(rest)), default=-1)
        male, female = last(_MALE, _FEMALE)
        nationality = None
        if max(saudi, non_saudi) > total:
            nationality = "non_saudi" if non_saudi > saudi else "saudi"
        sex = None
        if max(male, female) > total:
            sex = "male" if male > female else "female"
        # No period from prose: the one near a figure is as often the one it is compared with («مقارنةً
        # بالربع الأول»); a workbook row names its own.
        return cls(sex=sex, nationality=nationality)

    def agrees(self, row: "Qualifiers") -> bool:
        """A label's qualifiers against its row's: sex and nationality equal, a total (None) only against a
        total; a row that names both (prose) does not constrain; a period, when both name one, equal."""
        for mine, theirs in ((self.sex, row.sex), (self.nationality, row.nationality)):
            if theirs != _AMBIGUOUS and mine != theirs:
                return False
        if self.quarter and row.quarter and self.quarter != row.quarter:
            return False
        return not (self.year and row.year and self.year != row.year)


@dataclass
class _Unit:
    """One place a passage states figures: a workbook cell (its row and column) or a sentence of prose."""

    context: str
    figures: set[str]
    words: set[str]
    qualifiers: Qualifiers


_SENTENCE = re.compile(r"(?<=[.!?؟])\s+(?=\D)|\n+")


def _units(text: str) -> list[_Unit]:
    lines = text.split("\n")
    columns = next((line[len("[columns] "):].split(" | ") for line in lines if line.startswith("[columns] ")), None)
    units: list[_Unit] = []
    if columns is not None:
        # A workbook chunk (ADR-0413): its title lines, then rows «label | v1 | v2 …» under [columns].
        titles = [line for line in lines if line.startswith(" | ") or line.startswith("[") and not line.startswith("[columns]")]
        title = " ".join(t.strip(" |[]") for t in titles if "|" not in t.strip(" |") or t.startswith(" | "))
        for line in lines:
            cells = line.split(" | ")
            if line.startswith("[") or len(cells) < 2 or not any(spans(c) for c in cells[1:]):
                continue
            for col, cell in enumerate(cells[1:], start=1):
                found = figures([cell])
                if not found:
                    continue
                label = columns[col] if col < len(columns) else ""
                row_and_column = f"{cells[0]} {label}"
                context = f"{title} {row_and_column}"
                units.append(_Unit(context, found, words([context]), Qualifiers.of(row_and_column)))
        return units
    # Prose (a PDF page, a document's text): one unit per figure, read with the text just before it, its
    # layout line breaks joined.
    flat = re.sub(r"\s+", " ", text)
    for start, end, figure in spans(flat):
        before = flat[max(0, start - PROSE_WINDOW) : start]
        stops = list(_FULL_STOP.finditer(before))
        if stops:
            before = before[stops[-1].end() :]
        context = before + flat[start : end + 40]
        units.append(_Unit(context, figures([flat[start:end]]), words([context]), Qualifiers.before(before)))
    return units


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
    _units: list[_Unit] | None = None

    @property
    def units(self) -> list[_Unit]:
        if self._units is None:
            self._units = _units(self.text)
        return self._units

    def holds(self, figure: str, groups: Sequence[Sequence[str]], qualifiers: Qualifiers | None = None) -> bool:
        """The figure sits in one of the passage's rows (a workbook cell's row and column, or a sentence) that
        also holds, for each group of labels with a word of its own, one of its words (the value's position,
        its measure), and whose qualifiers agree with the label's (ADR-0414): a total's label only in a total's
        row or column, «الذكور» only in the men's, the period its own. So 4.8, Saudi men's unemployment, is not
        «معدل البطالة الإجمالي», and 84.3 under «ذكور» in a participation table is not a share of employed men."""
        for unit in self.units:
            if figure not in unit.figures:
                continue
            if qualifiers is not None and not qualifiers.agrees(unit.qualifiers):
                continue
            if all(self._worded(labels, unit) for labels in groups):
                return True
        return False

    def _worded(self, labels: Sequence[str], unit: _Unit) -> bool:
        wanted = words(labels)
        if not wanted or wanted & unit.words:
            return True
        # A label in a script the passage does not use was translated (ADR-0396): the figure decides.
        text = " ".join(labels)
        return not ((ARABIC.search(text) and ARABIC.search(unit.context)) or (LATIN.search(text) and LATIN.search(unit.context)))


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
    # Charts that lost the categories with no value found (ADR-0416): (chart title, the categories removed).
    trimmed: list[tuple[str, list[str]]] = field(default_factory=list)
    # Elements of value checked (KPI rows, charts, tables) and those kept: a sourced report with none kept has
    # found nothing on its subject (ADR-0416).
    checked: int = 0
    kept: int = 0
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
        # The report's period (its head's `period`): a value's label that names none is of this period.
        self.period: str | None = None
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
            label = " ".join(text for group in groups for text in group if text)
            qualifiers = Qualifiers.of(label)
            if self.period and qualifiers.quarter is None and qualifiers.year is None:
                period = Qualifiers.of(self.period)
                qualifiers = Qualifiers(qualifiers.sex, qualifiers.nationality, period.quarter, period.year)
            for passage in cited:
                if passage.holds(figure, groups, qualifiers):
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
            # A category no series has a value for leaves the chart (ADR-0416): no empty group of bars.
            empty = [i for i in range(len(section.labels)) if all(i >= len(s.data) or s.data[i] is None for s in section.series)]
            if empty and len(empty) < len(section.labels):
                removed = [section.labels[i] for i in empty]
                keep = [i for i in range(len(section.labels)) if i not in empty]
                section.labels = [section.labels[i] for i in keep]
                for series in section.series:
                    series.data = [series.data[i] if i < len(series.data) else None for i in keep]
                self.result.trimmed.append((title, removed))
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
        self.result.checked += 1
        # Nothing found, or nothing to find (a chart or KPI row the model left all null): the element leaves.
        if (total and not found) or (not total and not isinstance(section, Table)):
            self.result.dropped.append(title)
            return None
        # A chart with fewer than two categories left says nothing as a chart (ADR-0416).
        if isinstance(section, Chart) and len(section.labels) < 2:
            self.result.dropped.append(title)
            return None
        self.result.kept += 1
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
        self.period = spec.period
        kept = [s for s in (self.element(section) for section in spec.sections) if s is not None]
        self.result.spec = spec.model_copy(update={"sections": kept})
        return self.result
