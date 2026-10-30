"""Figures and label words, the shared rules of a report's verification (reports PR, ADR-0381, ADR-0397,
ADR-0399, ADR-0404; used per element by app/artifacts/verify.py, ADR-0407).

A figure is a run of digits (Western or Arabic-Indic) with its thousands separators (comma, the Arabic
separator, a narrow or plain no-break space) and one decimal part, not glued to a letter before it ("Q1",
"H2", "COVID-19" are names, not figures). Its canonical form drops the separators, leading zeros and
trailing decimal zeros, so «1,250», «١٬٢٥٠» and 1250.0 are one figure. On the source side number words
are folded to digits first (ADR-0359), a date gives its parts, and a figure stated with more than two
decimals (a workbook cell, 83.60299013) also gives its rounding half up to one and two decimals (ADR-0404);
no other rounding, no unit changes and no arithmetic are matched.

A figure carries its sign (review round 1 of PR #17, finding 1, ADR-0425): «-0.1», «−0.1» and «+0.1» are read
with it, a sign glued to the figure and not to a word or a number before it («15-24» is a range, not -24), and
the canonical form keeps a minus. In prose, a figure stated as the amount of a fall («انخفض … بمقدار 0.1»،
"fell by 0.1") is negative and of a rise positive (`by_wording`); a level («انخفض إلى 3.0») keeps its sign.

A label's words are light-stemmed with their plural and nisba endings folded («سعوديون», «للسعوديين» and
«سعودي» are one word), three letters or more, generic words such as «عدد» or "total" left out (ADR-0399).

Linear in the text: one regular-expression pass per text, set lookups."""

import re
from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal

from app.text.arabic import fold_number_words, normalize, search_terms

_INDIC = str.maketrans(
    {**{a: str(i) for i, a in enumerate("٠١٢٣٤٥٦٧٨٩")}, **{a: str(i) for i, a in enumerate("۰۱۲۳۴۵۶۷۸۹")}}
)
_DIGIT = r"[0-9٠-٩۰-۹]"
_DATE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}/\d{2,4}")
# Thousands separators: the comma, the Arabic one, and the narrow and plain no-break spaces of the SI
# style ("3 872"), which a model writes too; a plain space never joins two figures.
# A sign glued to the figure, with no letter, digit or decimal mark before it (ADR-0425).
_SIGN = r"(?:(?<![\w.,٫٬])[-−+](?=[0-9٠-٩۰-۹]))?"
FIGURE = re.compile(
    rf"{_SIGN}(?:{_DIGIT}{{1,3}}(?:[,٬\u202f\u00a0]{_DIGIT}{{3}})+(?:[.٫]{_DIGIT}+)?|{_DIGIT}+(?:[.٫]{_DIGIT}+)?)"
)


def canonical(raw: str) -> str:
    """A figure's canonical form: Western digits, no separators, no leading or trailing zeros, a minus kept
    (never on zero) and a plus dropped."""
    text = raw.strip().translate(_INDIC).replace(",", "").replace("٬", "").replace("\u202f", "").replace("\u00a0", "")
    text = text.replace("٫", ".")
    negative = text[:1] in ("-", "−")
    text = text.lstrip("-−+")
    whole, _, fraction = text.partition(".")
    whole = whole.lstrip("0") or "0"
    fraction = fraction.rstrip("0")
    out = f"{whole}.{fraction}" if fraction else whole
    return f"-{out}" if negative and out != "0" else out


def negated(figure: str) -> str:
    """A canonical figure with its sign turned."""
    if figure == "0":
        return figure
    return figure[1:] if figure.startswith("-") else f"-{figure}"


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
    if text[start] in "-−+":  # a sign is never glued to what precedes it (FIGURE's lookbehind)
        return False
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
    value = Decimal(figure)  # a minus rounds with it
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


# Words a label shares with almost any statistical passage; they never make a match on their own.
_GENERIC = frozenset(
    w
    for text in (
        "من في على الى عن مع حسب بين خلال هذا هذه التي الذي ذلك عدد نسبه اجمالي مجموع معدل قيمه متوسط"
        " المملكه سنه عام الربع الاول الثاني الثالث الرابع شهر مليون الف"
        # Words nearly every statistical table's title holds («التوزيع النسبي … حسب الجنسية والجنس - سلسلة
        # زمنية»): they never tell one measure from another (item 6 run 7, ADR-0410).
        " توزيع نسبي جنس جنسيه سلسله زمنيه احصاءات",
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


# The words of a change in prose (ADR-0425), matched on normalized text: a direction, then the amount it
# introduces right before the figure («بمقدار 0.1»، «مقداره 0.1»، «بنسبة 2.5»، "by 0.1"). A level («إلى 3.0»،
# "to 3.0") is not an amount.
_FALL = re.compile(
    r"انخفض|انخفاض|بانخفاض|تراجع|بتراجع|هبط|هبوط|نقص|انكمش|انكماش|تدني|\b(?:decreas|declin|fell|fall|drop|lower|down)\w*",
    re.I,
)
_RISE = re.compile(r"ارتفع|ارتفاع|بارتفاع|زاد|زياده|بزياده|نما|نمو|صعد|تحسن|\b(?:increas|rose|rise|grew|grow|up)\w*", re.I)
_AMOUNT = re.compile(r"(?:بمقدار|مقداره|مقدارها|قدره|قدرها|بنسبه|نسبته|نسبتها|\bby)\s*$", re.I)
_CLAUSE = re.compile(r"[.!?؟؛;،,]")


def by_wording(before: str) -> int:
    """-1 when the text just before a figure states it as the amount of a fall, +1 of a rise, 0 otherwise
    (a level, or no direction in its clause)."""
    text = normalize(before[-160:])
    if not _AMOUNT.search(text):
        return 0
    clause = _CLAUSE.split(text)[-1]
    falls = [m.end() for m in _FALL.finditer(clause)]
    rises = [m.end() for m in _RISE.finditer(clause)]
    if not falls and not rises:
        return 0
    return -1 if max(falls, default=-1) > max(rises, default=-1) else 1


def signed(figure: str, before: str) -> str:
    """A prose figure with the sign its wording gives it: an unsigned amount of a fall is negative."""
    if by_wording(before) < 0 and not figure.startswith("-"):
        return negated(figure)
    return figure


ARABIC = re.compile(r"[\u0600-\u06ff]")
LATIN = re.compile(r"[A-Za-z]")


def words(texts: Iterable[str]) -> set[str]:
    """The light-stemmed words of some texts, three letters or more, digits and generic words left out."""
    found: set[str] = set()
    for text in texts:
        if text:
            found.update(
                _key(w) for w in search_terms(text) if len(w) >= 3 and not w.isdigit() and w not in _GENERIC
            )
    return found
