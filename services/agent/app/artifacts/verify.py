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

from app.artifacts.figures import ARABIC, LATIN, canonical, canonical_number, figures, signed, spans, words
from app.artifacts.spec import Callout, Chart, KpiRow, ReportSpec, Table, Text
from app.text.arabic import fold_number_words, normalize

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
        # No quarter from prose: the one near a figure is as often the one it is compared with («مقارنةً
        # بالربع الأول»); a workbook row names its own. The year is the one named nearest (`nearest_year`).
        return cls(sex=sex, nationality=nationality)

    def with_year(self, year: str | None) -> "Qualifiers":
        return Qualifiers(self.sex, self.nationality, self.quarter, year if year is not None else self.year)

    def agrees(self, row: "Qualifiers") -> bool:
        """A label's qualifiers against its row's: sex and nationality equal, a total (None) only against a
        total; a row that names both (prose) does not constrain; a period, when both name one, equal."""
        for mine, theirs in ((self.sex, row.sex), (self.nationality, row.nationality)):
            if theirs != _AMBIGUOUS and mine != theirs:
                return False
        if self.quarter and row.quarter and self.quarter != row.quarter:
            return False
        return not (self.year and row.year and self.year != row.year)


def nearest_year(before: str, after: str) -> str | None:
    """The year a prose figure is of (review round 1 of PR #17, finding 3, ADR-0425): the one named with it,
    after it and before the next figure in its sentence («من 50.2 في عام 2023 إلى 51.4 في عام 2024»: 50.2 is
    2023's, 51.4 2024's; «… 6.5 % في الربع الثاني من عام 2026»), else the last one named before it in its
    sentence («في عام 2026 بلغ المعدل 6.5»)."""
    following = spans(_FULL_STOP.split(after, maxsplit=1)[0])
    if following and _YEAR.fullmatch(following[0][2]):
        return following[0][2]
    back = _YEAR.findall(before)
    return back[-1] if back else None


# Words that qualify a value's group rather than name its measure (ADR-0414's qualifiers, ADR-0425).
QUALIFIER_WORDS = words([
    "ذكور الذكور ذكر رجال الرجال اناث الاناث الإناث انثى نساء النساء سعوديين السعوديين سعوديون السعوديون سعودي "
    "سعوديات السعوديات غير male males men female females women non nationals",
])


@dataclass
class _Unit:
    """One place a passage states figures: a workbook cell (its row and column) or a sentence of prose.
    `cells`: the words of the cell's own row and column (a sentence's words in prose); `table`: the words of
    its table's own title lines, never the workbook's title or the sheet's name (ADR-0425)."""

    context: str
    figures: set[str]
    words: set[str]
    qualifiers: Qualifiers
    cells: set[str] = field(default_factory=set)
    table: set[str] = field(default_factory=set)


_SENTENCE = re.compile(r"(?<=[.!?؟])\s+(?=\D)|\n+")


_TABLE_NUMBER = re.compile(r"^\s*(?:\|\s*)?(?:جدول|table)\s*\(?[\w.\-]*\)?\s*$", re.I)


def _title_lines(lines: list[str]) -> tuple[list[str], list[str]]:
    """A workbook chunk's preamble titles (ADR-0413): (all of them, the table's own). Titles are the one-cell
    lines before the [columns] line; when there are two or more, the first is the workbook's title, the same on
    every sheet («إحصاءات سوق العمل الربع الثاني 2026»), and the rest the table's; a table number line
    («جدول (3-1)») and the sheet's name («[3-1]») name nothing (ADR-0425)."""
    titles = []
    for line in lines:
        if line.startswith("[columns]"):
            break
        if line.startswith("[") or _TABLE_NUMBER.match(line):
            continue
        if line.startswith(" | ") or "|" not in line:
            titles.append(line.strip(" |"))
    own = titles[1:] if len(titles) >= 2 else titles
    return titles, own


def _units(text: str) -> list[_Unit]:
    lines = text.split("\n")
    columns = next((line[len("[columns] "):].split(" | ") for line in lines if line.startswith("[columns] ")), None)
    units: list[_Unit] = []
    if columns is not None:
        # A workbook chunk (ADR-0413): its title lines, then rows «label | v1 | v2 …» under [columns].
        titles, own = _title_lines(lines)
        title = " ".join(titles)
        table_words = words(own)
        table_year = Qualifiers.of(" ".join(own)).year
        rows = lines[next(i for i, line in enumerate(lines) if line.startswith("[columns] ")) + 1 :]
        for line in rows:  # the preamble's titles are no rows, though one may name a year (ADR-0425)
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
                qualifiers = Qualifiers.of(row_and_column)
                if qualifiers.year is None:
                    qualifiers = qualifiers.with_year(table_year)
                units.append(_Unit(context, found, words([context]), qualifiers,
                                   cells=words([row_and_column]), table=table_words))
        return units
    # Prose (a PDF page, a document's text): one unit per figure, read with the text just before it, its
    # layout line breaks joined, and number words folded to digits first, as figures() reads them: «مدتها واحد
    # وعشرون يوما» states 21. A figure stated as the amount of a fall is negative (ADR-0425), and of the year
    # named nearest to it.
    flat = fold_number_words(re.sub(r"\s+", " ", text))
    for start, end, figure in spans(flat):
        before = flat[max(0, start - PROSE_WINDOW) : start]
        stops = list(_FULL_STOP.finditer(before))
        if stops:
            before = before[stops[-1].end() :]
        context = before + flat[start : end + 40]
        found = {signed(f, before) for f in figures([flat[start:end]])}
        year = nearest_year(before, flat[end : end + PROSE_WINDOW]) if not _YEAR.fullmatch(figure) else None
        sentence = words([context])
        units.append(_Unit(context, found, sentence, Qualifiers.before(before).with_year(year), cells=sentence))
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
    _words: set[str] | None = None

    @property
    def units(self) -> list[_Unit]:
        if self._units is None:
            self._units = _units(self.text)
        return self._units

    def holds(
        self,
        figure: str,
        position: Sequence[str] = (),
        measure: Sequence[str] = (),
        qualifiers: Qualifiers | None = None,
        *,
        year: str | None = None,
    ) -> bool:
        """The figure, its sign included, sits in one of the passage's rows (a workbook cell's row and column, or
        a sentence) whose qualifiers agree with the label's (ADR-0414) and which names the value's position and
        its measure (ADR-0425):
        - `position` (a chart point's category, a table cell's row label) shares a word with the cell's own row
          and column (the sentence, in prose);
        - `measure` (a KPI's label, a series or column with its element's title, a sentence) shares a word with
          what the row and column name beyond the position and the qualifiers, or, when they name nothing more
          (a time series' rows of quarters), with the table's own title; never with the workbook's title or the
          sheet's name. So «معدل المشاركة» is not 3.0 in sheet 1's «معدل البطالة» row because the workbook
          speaks of «سوق العمل»;
        - `year`, a position's own year («2023» as a category), only in a row of that year."""
        for unit in self.units:
            if figure not in unit.figures:
                continue
            if qualifiers is not None and not qualifiers.agrees(unit.qualifiers):
                continue
            if year is not None and unit.qualifiers.year != year:
                continue
            if self._named(position, measure, unit):
                return True
        return False

    def _named(self, position: Sequence[str], measure: Sequence[str], unit: _Unit) -> bool:
        placed: set[str] = set()
        wanted = words(position)
        if wanted:
            placed = wanted & unit.cells
            if not placed and not self._translated(position, unit):
                return False
        wanted = words(measure) - QUALIFIER_WORDS
        if not wanted:
            return True
        rest = unit.cells - QUALIFIER_WORDS - placed
        return bool(wanted & (rest or unit.table)) or self._translated(measure, unit)

    @staticmethod
    def _translated(labels: Sequence[str], unit: _Unit) -> bool:
        """A label in a script the passage does not use was translated (ADR-0396): the figure decides."""
        text = " ".join(labels)
        return not ((ARABIC.search(text) and ARABIC.search(unit.context)) or (LATIN.search(text) and LATIN.search(unit.context)))

    def names(self, text: str) -> bool:
        """Some word of a table's name cell (a row's label, a category) is in this passage (ADR-0425)."""
        wanted = words([text]) - QUALIFIER_WORDS
        if not wanted:
            return True
        if self._words is None:
            self._words = words([self.text])
        return bool(wanted & self._words) or self._translated([text], _Unit(self.text, set(), set(), Qualifiers()))


# A page's prose cut into sentences, each keeping its closing mark and space (ADR-0425).
_PAGE_SENTENCE = re.compile(r"(?:[^.!?؟]|\.(?=\d))+(?:[.!?؟]+\s*|$)|[.!?؟]+\s*")
_FIGURES_OUT = re.compile(r"[-−+]?\d[\d.,٫٬]*")


_TOTAL_LABEL = re.compile(r"(?:^|\s|\()(?:ال|لل|ل)?(?:اجمالي|مجموع)(?:\s|\)|$)|\btotal\b|\boverall\b", re.I)


def is_total(label: str) -> bool:
    return bool(_TOTAL_LABEL.search(normalize(label)))


def _without_totals(chart) -> None:
    """A total beside its own parts leaves the chart (owner decision on PR #17, ADR-0418): a «الإجمالي» category
    among the categories it sums, or a total series among the series it sums. A chart that is a total alone
    keeps it."""
    totals = [i for i, label in enumerate(chart.labels) if is_total(label)]
    if totals and len(totals) < len(chart.labels):
        keep = [i for i in range(len(chart.labels)) if i not in totals]
        chart.labels = [chart.labels[i] for i in keep]
        for series in chart.series:
            series.data = [series.data[i] if i < len(series.data) else None for i in keep]
    total_series = [s for s in chart.series if is_total(s.name)]
    if total_series and len(total_series) < len(chart.series):
        chart.series = [s for s in chart.series if not is_total(s.name)]


def _values(section) -> int:
    """The values an element shows: a KPI's value and change, a chart's points, a table's figures."""
    if isinstance(section, KpiRow):
        return sum(1 for item in section.items for v in (item.value, item.delta) if v is not None)
    if isinstance(section, Chart):
        return sum(1 for series in section.series for v in series.data if v is not None)
    if isinstance(section, Table):
        return sum(len(spans(str(cell))) for row in section.rows for cell in row[1:] if cell is not None)
    return 0


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
    # Sentences of text and callouts whose figures were not found in their cited passages (ADR-0425).
    removed: list[str] = field(default_factory=list)


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
                # A general report counts its values too: the version's `figures`, all of them general.
                self.result.values += _values(section)
            return section
        title = getattr(section, "title", None) or self.untitled
        if isinstance(section, Text | Callout):
            # Checked like a KPI against the section's own cited passages only (ADR-0425); a sentence whose
            # figure is not found leaves the page and is named in the reply.
            cited = [self.passages[s] for s in section.sources if s in self.passages]
            section.markdown = self._text(section.markdown, cited)
            section.sources = [s for s in section.sources if s in self.passages]
            return section if section.markdown.strip() else None
        cited = [self.passages[s] for s in section.sources if s in self.passages]
        if not cited or "general" in section.sources:
            self.result.dropped.append(title)
            return None
        uses: dict[str, Use] = {}
        missing: list[str] = []
        total = found = 0

        def check(value, name: str, *, position: Sequence[str] = (), measure: Sequence[str] = (),
                  period: Sequence[str] = ()) -> bool:
            """`period`: the labels a value's period is read from, most specific first (its category or row,
            then its series or column, then its element's title), the report's period last (ADR-0425)."""
            nonlocal total, found
            total += 1
            figure = canonical_number(value) if isinstance(value, int | float) else canonical(str(value))
            qualifiers = Qualifiers.of(" ".join(t for t in (*position, *measure) if t))
            own = Qualifiers.of(" ".join(t for t in position if t)) if position else Qualifiers()
            year = own.year
            for label in (*period, self.period or ""):
                found_period = Qualifiers.of(label or "")
                if found_period.year or found_period.quarter:
                    qualifiers = Qualifiers(qualifiers.sex, qualifiers.nationality, found_period.quarter,
                                            found_period.year)
                    break
            else:
                qualifiers = Qualifiers(qualifiers.sex, qualifiers.nationality)
            for passage in cited:
                if passage.holds(figure, position, measure, qualifiers, year=year):
                    found += 1
                    self._use(uses, section, title, passage)
                    if passage.ref not in self.result.refs:
                        self.result.refs.append(passage.ref)
                    return True
            missing.append(name)
            return False

        if isinstance(section, KpiRow):
            for item in section.items:
                # The KPI's own label names its measure; its row's title only when the label names none.
                measure = [item.label] if words([item.label]) - QUALIFIER_WORDS else [item.label, section.title or ""]
                period = [item.label, section.title or ""]
                if item.value is not None and not check(item.value, item.label, measure=measure, period=period):
                    item.value = None
                if item.delta is not None and not check(item.delta, f"{item.label} ({self.change})", measure=measure,
                                                        period=period):
                    item.delta = None
        elif isinstance(section, Chart):
            _without_totals(section)
            if len(section.labels) < 2:
                self.result.dropped.append(title)
                return None
            for series in section.series:
                for i, value in enumerate(series.data):
                    if value is None:
                        continue
                    category = section.labels[i] if i < len(section.labels) else ""
                    if not check(value, f"{category} · {series.name}", position=[category],
                                 measure=[series.name, section.title], period=[category, series.name, section.title]):
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
                    if cell is None:
                        continue
                    raw = fold_number_words(str(cell))  # a figure in words is checked as one (ADR-0425)
                    found_spans = spans(raw)
                    column = section.columns[c] if c < len(section.columns) else ""
                    if c == 0 or not found_spans:
                        # A name (a row's label, a category, a text cell): counted only when its cited passages
                        # name it too, so a table of names the sources never mention keeps nothing (ADR-0425).
                        if not words([raw]) - QUALIFIER_WORDS:
                            continue
                        total += 1
                        named = next((p for p in cited if p.names(raw)), None)
                        if named is not None:
                            found += 1
                            self._use(uses, section, title, named)
                        elif c > 0:
                            row[c] = MISSING
                            missing.append(f"{row[0]} · {column}")
                        else:
                            missing.append(str(cell))
                        continue
                    name = f"{row[0]} · {column}"
                    if not all(check(figure, name, position=[str(row[0] or "")], measure=[column, section.title],
                                     period=[str(row[0] or ""), column, section.title])
                               for _, _, figure in found_spans):
                        row[c] = MISSING
        self.result.values += total
        self.result.found += found
        self.result.unavailable += total - found
        self.result.checked += 1
        # Nothing found, or nothing to find (an element the model left all null, a table with nothing its
        # passages name): the element leaves, and a sourced report counts only elements verified (ADR-0425).
        if not found:
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

    def _text(self, markdown: str, cited: Sequence[Passage]) -> str:
        """Prose figures checked like a KPI's (ADR-0425), sentence by sentence, against the section's cited
        passages only: each figure (number words folded, its sign from its wording) with the words of its
        sentence before it as its measure, the qualifiers it names and the year named nearest to it, the
        report's period otherwise. A sentence holding a figure not found leaves the text and is named in the
        reply; one with no figure stays."""
        out: list[str] = []
        for line in markdown.split("\n"):
            bullet = re.match(r"^\s*(?:[-*•]|\d+[.)])\s+", line)
            lead = bullet.group(0) if bullet else ""
            kept = []
            for sentence in _PAGE_SENTENCE.findall(line[len(lead):]):
                if sentence.strip() and not self._sentence_holds(sentence, cited):
                    self.result.removed.append(sentence.strip())
                    continue
                kept.append(sentence)
            body = "".join(kept).strip()
            if body or not line[len(lead):].strip():
                out.append(lead + body if body else line)
        return "\n".join(out).strip("\n")

    def _sentence_holds(self, sentence: str, cited: Sequence[Passage]) -> bool:
        plain = fold_number_words(sentence.replace("**", "").replace("__", ""))
        for start, end, figure in spans(plain):
            before = plain[:start]
            figure = signed(figure, before)
            year = nearest_year(before, plain[end:]) if not _YEAR.fullmatch(figure.lstrip("-")) else None
            if year is None:
                year = Qualifiers.of(self.period or "").year
            qualifiers = Qualifiers.before(before).with_year(year)
            # Only the words of its own sentence before it say what the figure is.
            measure = [_FIGURES_OUT.sub(" ", before)]
            holding = next((p for p in cited if p.holds(figure, (), measure, qualifiers)), None)
            if holding is None:
                return False
            if holding.ref not in self.result.refs:
                self.result.refs.append(holding.ref)
        return True

    # ------------------------------------------------------------------ the whole spec
    def spec(self, spec: ReportSpec) -> Verified:
        self.period = spec.period
        kept = [s for s in (self.element(section) for section in spec.sections) if s is not None]
        self.result.spec = spec.model_copy(update={"sections": kept})
        return self.result
