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
clause), and stranded leading punctuation put back: a colon at the first boundary between Arabic and a value that follows
it, any other mark at the end of the line. No line is ever reversed on a guess from its words
(the old «ends in لا» and reversed-particle heuristics are gone: «قليلا جدا» is left alone).
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
