"""The figure check of a report (reports PR, D2, I3, ADR-0381; source labels, ADR-0388).

A figure is a run of digits (Western or Arabic-Indic) with its thousands separators and one decimal
part, not glued to a letter before it ("Q1", "H2", "COVID-19" are names, not figures). Its canonical
form drops the separators, leading zeros and trailing decimal zeros, so «1,250», «١٬٢٥٠» and 1250.0
are one figure. Every figure the page shows carries exactly one label, the first source that holds the
same canonical figure (ADR-0388): a passage fetched for the answer or its source's metadata (titles,
publishers, dates), labelled with its knowledge base's name or its file's title; a capability's
findings, labelled with the capability's name; the user's message; on a follow-up, the label the
previous version gave it. Number words on the source side are folded to digits first (phase 08,
ADR-0359). Anything else is the model's own and is labelled general knowledge. Rounding, unit changes
(1.2 million for 1,200,000) and arithmetic are not matched: such a figure reads general knowledge,
the safe side of D2.

Linear in the text: one regular-expression pass, set lookups."""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.text.arabic import fold_number_words

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


def figures(texts: Iterable[str]) -> set[str]:
    """The canonical figures of source texts, number words folded to digits first."""
    found: set[str] = set()
    for text in texts:
        if text:
            folded = fold_number_words(text)
            found.update(c for _, _, c in spans(folded))
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


# A label's kinds: where its figure was found. `general` is the model's own knowledge (D2).
KINDS = ("kb", "file", "source", "capability", "message", "general")


@dataclass(frozen=True)
class Label:
    kind: str
    text: str

    def stored(self) -> dict[str, str]:
        return {"kind": self.kind, "text": self.text}


class FigureLabels:
    """Each canonical figure's one label: the first source added that holds it, else general knowledge."""

    def __init__(self, general: Label):
        self.general = general
        self._of: dict[str, Label] = {}

    def add(self, texts: Iterable[str], label: Label) -> None:
        for figure in figures(texts):
            self._of.setdefault(figure, label)

    def keep(self, stored: Mapping[str, Mapping[str, str]]) -> None:
        """A previous version's labels (D4): a figure it labelled keeps that label."""
        for figure, label in stored.items():
            if isinstance(label, Mapping) and label.get("kind") in KINDS and label.get("text"):
                self._of.setdefault(figure, Label(str(label["kind"]), str(label["text"])))

    def of(self, figure: str) -> Label:
        return self._of.get(figure, self.general)
