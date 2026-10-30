"""Arabic lines out of pypdf (phase 04; owner decision A1, ADR-0173).

pypdf already returns the letters of every right-to-left run in logical order, whatever order
the PDF drew them in. What it does not undo:
- a PDF whose producer shaped the text itself (Arabic presentation forms, U+FB50–U+FDFF,
  U+FE70–U+FEFF) also laid the line out in visual order, so a line that mixes Arabic with
  digits or Latin comes back with its runs in visual order, left to right: «2031 الإصدار
  الثالث لعام». That is the one strong signal this module acts on: presentation forms and a
  digit or Latin run in the same line. The pieces of the line (each left-to-right run, and
  the text between two of them, which pypdf turned already) are then put back in logical
  order, no piece's own text touched, so «من عام 2027 إلى عام 2030» keeps its numbers where
  they were;
- a PDF that kept logical order (LibreOffice and most modern producers) comes back in the
  right order, but the spaces at a boundary between Arabic and a digit or Latin run are
  misplaced («عام2027  إلى») and a colon between an Arabic label and its value can move to
  the start of the line («:رقم الهوية1012345672»).

So every line gets its boundaries spaced, its spaces collapsed (none before a mark that ends a
clause), and stranded leading punctuation put back: a colon at the first boundary between
Arabic and a value that follows it, any other mark at the end of the line. No line is ever
reversed on a guess from its words (the old «ends in لا» and reversed-particle heuristics are
gone: «قليلا جدا» is left alone).
NFKC then folds presentation forms to ordinary letters."""

import re
import unicodedata

_ARABIC = "؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿"
_PRESENTATION = re.compile("[ﭐ-﷿ﹰ-﻿]")
_LTR = "0-9A-Za-z٠-٩۰-۹"
_HAS_LTR = re.compile(f"[{_LTR}]")
# A left-to-right run: digits and Latin letters with what lies between them that is not
# Arabic («Oasis Cloud», «noura.test@example.org», «7:30»). pypdf turns everything else of a
# line (Arabic and the neutral text around it) into logical order already.
_LTR_RUN = re.compile(f"[{_LTR}](?:[^{_ARABIC}]*[{_LTR}])?")
_GLUED_AFTER_ARABIC = re.compile(f"(?<=[{_ARABIC}])(?=[{_LTR}])")
_GLUED_BEFORE_ARABIC = re.compile(f"(?<=[{_LTR}])(?=[{_ARABIC}])")
_SPACES = re.compile(r"[ \t]+")
# A space before a mark that ends a clause («عام 2030 .») is never the document's own.
_SPACE_BEFORE_MARK = re.compile(r"[ \t]+(?=[.،؛:!?؟](?:\s|$))")
# Punctuation stranded at the start of an Arabic line by a reader that placed it by position.
_LEADING = re.compile(f"^([.!?؟،؛:：]+)\\s*(?=[{_ARABIC}])")
_VALUE_BOUNDARY = re.compile(f"(?<=[{_ARABIC}])\\s+(?=[{_LTR}])")


def is_visual(line: str) -> bool:
    """The strong signal: presentation forms (a producer that shaped and laid out the text
    itself) and a digit or Latin run, whose place in the line pypdf left in visual order."""
    return bool(_PRESENTATION.search(line) and _HAS_LTR.search(line))


def runs_to_logical(line: str) -> str:
    """The line's pieces in reverse order: what read left to right now reads right to left.
    A piece is a left-to-right run or the text between two of them, which pypdf already put in
    logical order, so no piece's own text is touched."""
    pieces: list[str] = []
    last = 0
    for match in _LTR_RUN.finditer(line):
        pieces += [line[last : match.start()], match.group(0)]
        last = match.end()
    pieces.append(line[last:])
    return "".join(reversed(pieces))


def _respace(line: str) -> str:
    line = _GLUED_BEFORE_ARABIC.sub(" ", _GLUED_AFTER_ARABIC.sub(" ", line))
    return _SPACE_BEFORE_MARK.sub("", _SPACES.sub(" ", line)).strip()


def _restrand(line: str) -> str:
    stranded = _LEADING.match(line)
    if not stranded:
        return line
    marks, rest = stranded.group(1), line[stranded.end() :]
    boundary = _VALUE_BOUNDARY.search(rest) if ":" in marks or "：" in marks else None
    if boundary:
        return f"{rest[: boundary.start()]}{marks} {rest[boundary.end() :]}"
    return rest.rstrip() + marks


def restore_line(line: str) -> str:
    """The line in logical order, spaced, with presentation forms folded (NFKC)."""
    if is_visual(line):
        line = runs_to_logical(line)
    return _restrand(_respace(unicodedata.normalize("NFKC", line)))


def restore_text(text: str) -> str:
    return "\n".join(restore_line(line) for line in text.splitlines())


# ------------------------------------------------------------------ numbers drawn right to left (ADR-0411)

# A piece of a number as a PDF may draw it: digits and the marks inside a number, nothing else.
MAX_NUMBER_CHARS = 12
_NUMBER_PIECE = re.compile(r"^[0-9٠-٩۰-۹.,٫٬]+$")


def reversed_numbers(pieces: list[tuple[float, float, str, float]]) -> list[tuple[list[str], str]]:
    """The numbers a page drew right to left, piece by piece: consecutive number pieces (in the order the
    content stream draws them) on one baseline, each touching the one before it on its left (its gap at most
    1.5 times its own width at the font's size, 0.6 em a character). pypdf joins them in stream order, so 6.5
    drawn «5», «.», «6» reads «5.6», 2026 drawn «6», «202» reads «6202» and 0.1 drawn «1», «0.» reads «10.».
    Separate numbers, a table row's cells drawn right to left, are far apart and never joined, so their
    order stays the row's. Returns each as (its pieces in stream order, the number in position order).
    `pieces` is (x, y, text, font size) of every drawn text piece, in stream order."""
    found: list[tuple[list[str], str]] = []
    group: list[tuple[float, float, str, float]] = []

    def close() -> None:
        # A mark drawn on its own at either end (a sentence's full stop, a comma) is the text's, not the
        # number's: it stays where it is.
        while group and not any(ch.isdigit() for ch in group[0][2]):
            group.pop(0)
        while group and not any(ch.isdigit() for ch in group[-1][2]):
            group.pop()
        joined = "".join(p[2] for p in group)
        # One number, not a run of labels drawn edge to edge (a chart axis's «2021 2022 … 2025»).
        if len(group) >= 2 and len(joined) <= MAX_NUMBER_CHARS:
            found.append(([p[2] for p in group], "".join(p[2] for p in sorted(group))))
        group.clear()

    for x, y, text, size in pieces:
        if not text:
            continue
        piece = text.strip()
        if not (piece and _NUMBER_PIECE.match(piece) and piece == text):
            close()
            continue
        if group:
            last = group[-1]
            touching = last[1] == y and 0 < last[0] - x <= 1.5 * len(piece) * 0.6 * size + 1
            if not touching:
                close()
        group.append((x, y, piece, size))
    close()
    return found


# A number's edges in pypdf's text: no digit or number mark before it, no digit after it, nor a mark that
# carries on into another digit («15.6» holds no «5.6»; «6.5» is not the start of «6.52»).
_DIGITS = "0-9٠-٩۰-۹"
_NUMBER_START = rf"(?<![{_DIGITS}.,٫٬])"
_NUMBER_END = rf"(?![{_DIGITS}]|[.,٫٬][{_DIGITS}])"


def restore_numbers(raw: str, reversed_: list[tuple[list[str], str]]) -> str:
    """`raw` (pypdf's text, in stream order) with each number drawn right to left in position order. A drawn
    run is found only as a whole number, its pieces joined as pypdf joined them (spaces allowed between
    them), so a number printed in the right order that shares its digits («15.6» beside a reversed «5.6») is
    never touched. When the text holds that run more often than the page drew it reversed (the same digits
    also printed in order), which one was reversed is ambiguous, and every one is left as extracted
    (review round 1 of PR #17, ADR-0421)."""
    runs: dict[tuple[str, ...], list[str]] = {}
    for pieces, number in reversed_:
        runs.setdefault(tuple(pieces), []).append(number)
    edits: list[tuple[int, int, str]] = []
    for pieces, numbers in runs.items():
        pattern = re.compile(_NUMBER_START + r"\s*".join(re.escape(p) for p in pieces) + _NUMBER_END)
        matches = list(pattern.finditer(raw))
        if len(matches) != len(numbers):
            continue
        edits += [(m.start(), m.end(), number) for m, number in zip(matches, numbers, strict=True)]
    out, at = [], 0
    for start, end, number in sorted(edits):
        if start < at:  # two runs over the same text: the first kept, the other left as extracted
            continue
        out += [raw[at:start], number]
        at = end
    out.append(raw[at:])
    return "".join(out)

