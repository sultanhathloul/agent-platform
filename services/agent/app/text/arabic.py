"""Arabic-aware normalization for matching (routing keywords now; guard
keyword lists and chunking later). Folds alef, yaa and taa-marbuta variants,
drops tatweel and (by default) diacritics, maps Arabic-Indic digits to ASCII,
case-folds and collapses whitespace."""

import re
import unicodedata

_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭ]")
_WHITESPACE = re.compile(r"\s+")
_TATWEEL = "ـ"
_FOLD = str.maketrans(
    {
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ٱ": "ا",
        "ى": "ي",
        "ة": "ه",
        **{arabic: str(i) for i, arabic in enumerate("٠١٢٣٤٥٦٧٨٩")},
        **{persian: str(i) for i, persian in enumerate("۰۱۲۳۴۵۶۷۸۹")},
    }
)


def normalize(text: str, *, keep_diacritics: bool = False) -> str:
    text = unicodedata.normalize("NFKC", text).replace(_TATWEEL, "")
    if not keep_diacritics:
        text = _DIACRITICS.sub("", text)
    text = text.translate(_FOLD).casefold()
    return _WHITESPACE.sub(" ", text).strip()


# Proclitic combinations light stemming strips, longest first: conjunctions, prepositions, the article.
_PROCLITICS = ("وبال", "وال", "بال", "كال", "فال", "لل", "ال", "و", "ب", "ل", "ف", "ك")


def light_stems(word: str) -> set[str]:
    """The normalized word, and what remains after stripping one proclitic combination
    (و، ب، ل، ف، ك، ال) when at least three letters are left. No suffixes, no roots."""
    word = normalize(word)
    stems = {word}
    for prefix in _PROCLITICS:
        if word.startswith(prefix) and len(word) - len(prefix) >= 3:
            stems.add(word[len(prefix) :])
    return stems


def contains_keyword(text: str, keyword: str) -> bool:
    """Substring match on normalized forms, so Arabic prefixes (ال، و، ب) still match."""
    needle = normalize(keyword)
    return bool(needle) and needle in normalize(text)


# Tashkeel (harakat, tanween, shadda, sukun, superscript alef) and Quranic marks.
_MARKS = frozenset(
    chr(c) for c in (*range(0x0610, 0x061B), *range(0x064B, 0x0660), 0x0670, *range(0x06D6, 0x06EE))
)
_DIGITS = {
    **{a: str(i) for i, a in enumerate("٠١٢٣٤٥٦٧٨٩")},
    **{p: str(i) for i, p in enumerate("۰۱۲۳۴۵۶۷۸۹")},
}
_LETTERS = {"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه"}


def fold_with_map(text: str, *, letters: bool = True) -> tuple[str, list[int]]:
    """Folds `text` for matching and keeps where every folded character came from:
    Arabic-Indic and Persian digits become ASCII digits, tatweel and tashkeel are
    removed, Unicode spaces become a plain space and, with `letters`, alef, alef
    maqsura and taa marbuta forms fold as in `normalize`. No case folding, so a regex
    can still use its own flags. `mapping[i]` is the index in `text` of folded
    character i; `span_in_source` turns folded offsets back into source offsets."""
    out: list[str] = []
    mapping: list[int] = []
    for index, char in enumerate(text):
        if char == _TATWEEL or char in _MARKS:
            continue
        if char in _DIGITS:
            char = _DIGITS[char]
        elif letters and char in _LETTERS:
            char = _LETTERS[char]
        elif char.isspace():
            char = " "
        out.append(char)
        mapping.append(index)
    return "".join(out), mapping


def span_in_source(mapping: list[int], start: int, end: int, source_length: int) -> tuple[int, int]:
    """Source offsets of the folded span [start, end): from the first folded character's
    source index to just after the last one's."""
    if start >= len(mapping):
        return source_length, source_length
    if end <= start:
        return mapping[start], mapping[start]
    return mapping[start], mapping[min(end, len(mapping)) - 1] + 1


# Light prefix stripping for keyword lists (README §10.2): a conjunction (و، ف), then a
# preposition (ب، ل), then the article (ال); لل is ل + ال with the alef elided.
_KEYWORD_PREFIXES = sorted(
    {c + p + a for c in ("", "و", "ف") for p in ("", "ب", "ل") for a in ("", "ال")} | {"لل", "ولل", "فلل"},
    key=len,
    reverse=True,
)


def keyword_stems(word: str, *, min_length: int = 3) -> set[str]:
    """The normalized word and every form left after stripping one prefix combination
    of ال، و، ب، ل، ف, keeping at least `min_length` letters."""
    word = normalize(word)
    stems = {word}
    for prefix in _KEYWORD_PREFIXES:
        if prefix and word.startswith(prefix) and len(word) - len(prefix) >= min_length:
            stems.add(word[len(prefix) :])
    return stems
