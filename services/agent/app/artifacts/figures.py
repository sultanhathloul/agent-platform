"""The figure check of a report (reports PR, D2, I3, ADR-0381; source labels, ADR-0388; one kind of source
per turn, ADR-0398 and ADR-0399).

A figure is a run of digits (Western or Arabic-Indic) with its thousands separators and one decimal
part, not glued to a letter before it ("Q1", "H2", "COVID-19" are names, not figures). Its canonical
form drops the separators, leading zeros and trailing decimal zeros, so «1,250», «١٬٢٥٠» and 1250.0
are one figure. Every figure the page shows carries exactly one label.

A turn draws from one kind of source (ADR-0398). On a sourced turn (knowledge bases toggled, files
attached, or a capability's findings) a figure is labelled with the first of this turn's sources that
holds it: a fetched passage only when the same passage also holds the figure's row or column label
(the words the page writes beside it: a table's row and column headers, a chart's category and series
names, the text around it), labelled with its knowledge base's name or its file's title; a source's
metadata (titles, publishers, dates); a capability's findings (same rule as a passage); the user's
message. Anything else is not in the findings and reads «غير متوفر» / "Not available" in place of the
number. On an unsourced turn no matching is done: every figure is the model's own, labelled general
knowledge. Nothing comes from an earlier turn. Number words on the source side are folded to digits
first (phase 08, ADR-0359). A source figure stated with more than two decimals (a statistical
workbook's cell, 83.60299013) also matches its value rounded half up to one or two decimals (83.6, 83.60),
as a page writes it (ADR-0404); no other rounding, no unit changes (1.2 million for 1,200,000) and no
arithmetic are matched: the safe side of D2.

A label matches a passage when one of its words is a word of the passage: words are light-stemmed and
their number and nisba endings folded («سعوديون», «للسعوديين» and «سعودي» are one word), and generic words
such as «عدد» or "total" are left out. A label with no letter in the passage's script (an English page over
an Arabic passage, ADR-0396) cannot be matched word for word: the figure alone decides there (ADR-0399).

Linear in the text: one regular-expression pass per text, set lookups."""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.text.arabic import fold_number_words, search_terms

_INDIC = str.maketrans(
    {**{a: str(i) for i, a in enumerate("٠١٢٣٤٥٦٧٨٩")}, **{a: str(i) for i, a in enumerate("۰۱۲۳۴۵۶۷۸۹")}}
)
_DIGIT = r"[0-9٠-٩۰-۹]"
_DATE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}/\d{2,4}")
# Thousands separators: the comma, the Arabic one, and the narrow and plain no-break spaces of the SI
# style ("3 872"), which a model writes too; a plain space never joins two figures.
FIGURE = re.compile(rf"{_DIGIT}{{1,3}}(?:[,٬\u202f\u00a0]{_DIGIT}{{3}})+(?:[.٫]{_DIGIT}+)?|{_DIGIT}+(?:[.٫]{_DIGIT}+)?")


def canonical(raw: str) -> str:
    """A figure's canonical form: Western digits, no separators, no leading or trailing zeros."""
    text = raw.translate(_INDIC).replace(",", "").replace("٬", "").replace("\u202f", "").replace("\u00a0", "")
    text = text.replace("٫", ".")
    whole, _, fraction = text.partition(".")
    whole = whole.lstrip("0") or "0"
    fraction = fraction.rstrip("0")
    return f"{whole}.{fraction}" if fraction else whole


def canonical_number(value: float | int) -> str:
    """A JSON number's canonical form, comparable with `canonical` of a written one."""
    if isinstance(value, bool):
        return str(value)
    if float(value).is_integer():
        return str(int(value))
    return canonical(repr(float(value)))


def _glued(text: str, start: int, end: int) -> bool:
    """The figure at `start` is part of a name: a letter right before it, a letter and a hyphen ("COVID-19"),
    or a Latin letter right after it ("5G", "3D")."""
    if end < len(text) and text[end].isascii() and text[end].isalpha():
        return True
    if start == 0:
        return False
    before = text[start - 1]
    if before.isalpha():
        return True
    return before in "-‐‑" and start >= 2 and text[start - 2].isalpha()


def spans(text: str) -> list[tuple[int, int, str]]:
    """(start, end, canonical) of each figure in a text."""
    return [(m.start(), m.end(), canonical(m.group(0))) for m in FIGURE.finditer(text) if not _glued(text, m.start(), m.end())]


def rounded(figure: str) -> list[str]:
    """A canonical figure with more than two decimals, rounded half up to one and two decimals (ADR-0404)."""
    whole, _, fraction = figure.partition(".")
    if len(fraction) <= 2:
        return []
    value = Decimal(figure)
    return [canonical(str(value.quantize(Decimal(step), rounding=ROUND_HALF_UP))) for step in ("0.1", "0.01")]


def figures(texts: Iterable[str]) -> set[str]:
    """The canonical figures of source texts, number words folded to digits first, with a precise figure's
    one- and two-decimal roundings."""
    found: set[str] = set()
    for text in texts:
        if text:
            folded = fold_number_words(text)
            for _, _, c in spans(folded):
                found.add(c)
                found.update(rounded(c))
            # A date written 2031-07-20 or 20/7/2031 gives its parts, as figures a page may state.
            for date in _DATE.findall(folded.translate(_INDIC)):
                found.update(canonical(part) for part in re.findall(r"\d+", date))
    return found


def source_texts(source: dict) -> list[str]:
    """What a source's metadata says (its title, publisher, KB name and date), for the allowed figures."""
    names = source.get("kb_name") or {}
    parts = [source.get("title"), source.get("publisher"), source.get("published_on")]
    if isinstance(names, dict):
        parts += [names.get("ar"), names.get("en")]
    return [str(p) for p in parts if p]


# A label's kinds: where its figure was found. `general` is the model's own knowledge (D2); `unavailable`
# is a figure a sourced turn's findings do not hold (ADR-0398).
KINDS = ("kb", "file", "source", "capability", "message", "general", "unavailable")

# Words a label shares with almost any statistical passage; they never make a match on their own.
_GENERIC = frozenset(
    w
    for text in (
        "من في على الى عن مع حسب بين خلال هذا هذه التي الذي ذلك عدد نسبه اجمالي مجموع معدل قيمه متوسط"
        " المملكه سنه عام الربع الاول الثاني الثالث الرابع شهر مليون الف",
        "the and for with from per total number rate share value average year quarter month saudi arabia"
        " kingdom million thousand percent",
    )
    for w in search_terms(text)
)
# Plural and nisba endings, folded so a label's word meets the passage's other form (ADR-0399).
_PLURAL = ("ون", "ين", "ات")
_NISBA = ("يه", "ه", "ي")


def _key(word: str) -> str:
    for endings in (_PLURAL, _NISBA):
        for ending in endings:
            if word.endswith(ending) and len(word) - len(ending) >= 3:
                word = word[: -len(ending)]
                break
    return word
_ARABIC = re.compile(r"[\u0600-\u06ff]")
_LATIN = re.compile(r"[A-Za-z]")


def words(texts: Iterable[str]) -> set[str]:
    """The light-stemmed words of some texts, three letters or more, digits and generic words left out."""
    found: set[str] = set()
    for text in texts:
        if text:
            found.update(
                _key(w) for w in search_terms(text) if len(w) >= 3 and not w.isdigit() and w not in _GENERIC
            )
    return found


@dataclass(frozen=True)
class Label:
    kind: str
    text: str

    def stored(self) -> dict[str, str]:
        return {"kind": self.kind, "text": self.text}


@dataclass(frozen=True)
class _Entry:
    label: Label
    words: frozenset[str]
    arabic: bool
    latin: bool
    needs_context: bool


class FigureLabels:
    """Each figure's one label from this turn's sources, first added first (ADR-0398, ADR-0399).

    `general` is the label of every figure on an unsourced turn, where `sourced` is false and nothing is
    matched; on a sourced turn a figure no source holds gets `unavailable`."""

    def __init__(self, general: Label, *, sourced: bool = False, unavailable: Label | None = None):
        self.general = general
        self.sourced = sourced
        self.unavailable = unavailable or general
        self._entries: list[_Entry] = []
        self._at: dict[str, list[int]] = {}

    def add(self, texts: Iterable[str], label: Label, *, passage: bool = False) -> None:
        """A source's texts. `passage`: a figure counts only with its row or column label in the same
        texts (a fetched chunk, a capability's findings); else the figure alone (metadata, the message)."""
        if not self.sourced:
            return
        texts = [t for t in texts if t]
        joined = "\n".join(texts)
        entry = _Entry(label, frozenset(words(texts)), bool(_ARABIC.search(joined)), bool(_LATIN.search(joined)), passage)
        self._entries.append(entry)
        for figure in figures(texts):
            self._at.setdefault(figure, []).append(len(self._entries) - 1)

    def of(self, figure: str, context: Sequence[str] = ()) -> Label:
        if not self.sourced:
            return self.general
        wanted: set[str] | None = None
        for index in self._at.get(figure, ()):
            entry = self._entries[index]
            if not entry.needs_context:
                return entry.label
            if wanted is None:
                wanted = words(context)
            if wanted & entry.words or (wanted and not _same_script(context, entry)):
                return entry.label
        return self.unavailable


def _same_script(context: Sequence[str], entry: _Entry) -> bool:
    """Whether the label is written in a script the passage has: else it was translated (ADR-0396)."""
    text = " ".join(context)
    return bool(_ARABIC.search(text)) and entry.arabic or bool(_LATIN.search(text)) and entry.latin
