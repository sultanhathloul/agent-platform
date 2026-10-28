"""The figure check of a report (reports PR, D2, I3, ADR-0381).

A figure is a run of digits (Western or Arabic-Indic) with its thousands separators and one decimal
part, not glued to a letter before it ("Q1", "H2", "COVID-19" are names, not figures). Its canonical
form drops the separators, leading zeros and trailing decimal zeros, so «1,250», «١٬٢٥٠» and 1250.0
are one figure. A figure is verified when the same canonical figure appears in the turn's sources:
the capabilities' findings, the passages fetched for the answer, the sources' own metadata (titles,
publishers, dates), the user's message, and, on a follow-up, the figures the previous version had
verified. Number words on the source side are folded to digits first (phase 08, ADR-0359). Anything
else is the model's own and is marked: wrapped in the kit's unverified marker in the text, flagged
point by point in the report-data block. Rounding, unit changes (1.2 million for 1,200,000) and
arithmetic are not matched: such a figure is marked, the safe side of D2.

Linear in the text: one regular-expression pass, set lookups."""

import re
from collections.abc import Iterable

from app.text.arabic import fold_number_words

_INDIC = str.maketrans(
    {**{a: str(i) for i, a in enumerate("٠١٢٣٤٥٦٧٨٩")}, **{a: str(i) for i, a in enumerate("۰۱۲۳۴۵۶۷۸۹")}}
)
_DIGIT = r"[0-9٠-٩۰-۹]"
_DATE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}/\d{2,4}")
FIGURE = re.compile(rf"{_DIGIT}{{1,3}}(?:[,٬]{_DIGIT}{{3}})+(?:[.٫]{_DIGIT}+)?|{_DIGIT}+(?:[.٫]{_DIGIT}+)?")


def canonical(raw: str) -> str:
    """A figure's canonical form: Western digits, no separators, no leading or trailing zeros."""
    text = raw.translate(_INDIC).replace(",", "").replace("٬", "").replace("٫", ".")
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
