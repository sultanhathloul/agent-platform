"""Arabic text out of PDFs (phase 04, ADR-0135).

Many PDFs hold Arabic as presentation forms (U+FB50–U+FDFF, U+FE70–U+FEFF) drawn in
visual order: the extracted line reads right to left reversed, each word's letters
backwards. `restore_line` detects such a line and puts it back in logical order; NFKC
then folds the presentation forms to ordinary letters (`app/text/arabic.normalize` and
the chunk text alike).

Detection, per line:
- with presentation forms: in logical order a word starts with an initial (or isolated)
  form and ends with a final (or isolated) form; reversed, the other way round;
- without them: in logical order Arabic words start with the article ال and common
  particles read في، من، على، إلى، عن; reversed, words end with لا and read يف، نم، ...
A line is reversed only when the visual evidence outweighs the logical evidence, so a
line already in logical order is never touched. Runs of digits and Latin letters keep
their own left-to-right order when a line is reversed, and brackets are mirrored.
Sentence punctuation left at the start of an Arabic line (a reader that reorders letters
but not neutral characters) moves back to its end."""

import re
import unicodedata

_ARABIC_RUN = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]+")
_LTR_RUN = re.compile(r"[0-9A-Za-z٠-٩۰-۹][0-9A-Za-z٠-٩۰-۹.,:/%@_\-]*")
_MIRROR = str.maketrans({"(": ")", ")": "(", "[": "]", "]": "[", "{": "}", "}": "{", "<": ">", ">": "<"})
# Sentence punctuation stranded at the start of an Arabic line by a reader that reordered
# the letters but not the neutral characters (pypdf does this on some lines).
_LEADING_PUNCTUATION = re.compile(r"^([.!?؟،؛:]+)\s*(?=[\u0600-\u06FF\uFB50-\uFDFF\uFE70-\uFEFF])")
_PARTICLES = ("في", "من", "على", "الى", "إلى", "عن", "مع", "هذا", "هذه", "التي", "الذي")
_PARTICLES_REVERSED = tuple(p[::-1] for p in _PARTICLES)


def _form(char: str) -> str | None:
    try:
        name = unicodedata.name(char)
    except ValueError:
        return None
    for form in ("INITIAL", "FINAL", "MEDIAL", "ISOLATED"):
        if f"{form} FORM" in name:
            return form
    return None


def _evidence(line: str) -> tuple[int, int]:
    """(logical, visual) evidence among the line's Arabic words."""
    logical = visual = 0
    for word in _ARABIC_RUN.findall(line):
        first, last = _form(word[0]), _form(word[-1])
        if first or last:
            if len(word) < 2:
                continue
            logical += (first == "INITIAL") + (last == "FINAL")
            visual += (first == "FINAL") + (last == "INITIAL")
            continue
        plain = unicodedata.normalize("NFKC", word)
        if len(plain) >= 4 and plain.startswith("ال"):
            logical += 1
        if len(plain) >= 4 and plain.endswith("لا"):
            visual += 1
        logical += plain in _PARTICLES
        visual += plain in _PARTICLES_REVERSED
    return logical, visual


def is_visual(line: str) -> bool:
    logical, visual = _evidence(line)
    return visual > logical


def reverse_visual(line: str) -> str:
    """The line in logical order: characters reversed, then every digit or Latin run
    reversed back so it reads left to right again, and brackets mirrored."""
    reversed_line = line[::-1].translate(_MIRROR)
    return _LTR_RUN.sub(lambda m: m.group(0)[::-1], reversed_line)


def restore_line(line: str) -> str:
    """The line in logical order with presentation forms folded (NFKC)."""
    if _ARABIC_RUN.search(line):
        if is_visual(line):
            line = reverse_visual(line)
        stranded = _LEADING_PUNCTUATION.match(line)
        if stranded:
            line = line[stranded.end() :].rstrip() + stranded.group(1)
    return unicodedata.normalize("NFKC", line)


def restore_text(text: str) -> str:
    return "\n".join(restore_line(line) for line in text.splitlines())
