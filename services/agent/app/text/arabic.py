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


_SEARCH_WORD = re.compile(r"\w+")


def search_stem(word: str) -> str:
    """One canonical form of a normalized word for full-text search (phase 04, I4): the
    longest proclitic combination of `light_stems` is stripped when at least three letters
    remain. The same word always gives the same stem, at indexing and at query time."""
    word = normalize(word)
    for prefix in _PROCLITICS:  # longest first
        if word.startswith(prefix) and len(word) - len(prefix) >= 3:
            return word[len(prefix) :]
    return word


def search_terms(text: str) -> list[str]:
    """The text's words as search stems, in order: normalized (alef, yaa and taa marbuta
    folded, tatweel and tashkeel removed, digits to ASCII, case folded) and light-stemmed."""
    return [search_stem(word) for word in _SEARCH_WORD.findall(normalize(text))]


def normalize_for_search(text: str) -> str:
    """`search_terms` joined by spaces: the `text_norm` of a chunk and of a query."""
    return " ".join(search_terms(text))


# Number words folded to digits for citation matching (owner decision of 2026-10-27, ADR-0359): an
# answer writing «21» or «واحد وعشرين» and a source writing «واحد وعشرون» then share the number.
# Keys are `normalize`d words (alef, yaa and taa marbuta folded); case endings and gender forms are
# listed, so عشرون/عشرين، اثنان/اثنين، ثلاث/ثلاثة fold alike.
_AR_UNITS = {
    "واحد": 1, "واحده": 1, "اثنان": 2, "اثنين": 2, "اثنتان": 2, "اثنتين": 2, "ثلاث": 3, "ثلاثه": 3,
    "اربع": 4, "اربعه": 4, "خمس": 5, "خمسه": 5, "ست": 6, "سته": 6, "سبع": 7, "سبعه": 7, "ثمان": 8,
    "ثماني": 8, "ثمانيه": 8, "تسع": 9, "تسعه": 9,
}
# Only before عشر/عشرة: «أحد عشر»، «إحدى عشرة»، «اثنا عشر»، «اثنتي عشرة».
_AR_TEEN_UNITS = {"احد": 1, "احدي": 1, "اثنا": 2, "اثني": 2, "اثنتا": 2, "اثنتي": 2}
_AR_TEN = {"عشر", "عشره"}
_AR_TENS = {
    stem + ending: value
    for stem, value in (("عشر", 20), ("ثلاث", 30), ("اربع", 40), ("خمس", 50), ("ست", 60), ("سبع", 70),
                        ("ثمان", 80), ("تسع", 90))
    for ending in ("ون", "ين")
}
_AR_HUNDREDS = {"مئه": 100, "مائه": 100, "مئتان": 200, "مئتين": 200, "مائتان": 200, "مائتين": 200}
_AR_HUNDREDS |= {
    unit + hundred: value * 100
    for unit, value in (("ثلاث", 3), ("اربع", 4), ("خمس", 5), ("ست", 6), ("سبع", 7), ("ثمان", 8), ("تسع", 9))
    for hundred in ("مئه", "مائه")
}
_AR_THOUSANDS = {"الف": 1000, "الفا": 1000, "الفان": 2000, "الفين": 2000, "الاف": 1000}
_EN_UNITS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
}
_EN_TEENS = {
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_EN_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
    "ninety": 90,
}
# A first number word may carry a conjunction and a preposition (وبعشرة، لثلاثين); a later one only «و».
_LEAD = ("وب", "ول", "وك", "فب", "فل", "و", "ف", "ب", "ل", "ك")
# Letters, with tashkeel and tatweel inside a word.
_NUMBER_WORD = re.compile(r"(?:[^\W\d_]|[\u0610-\u061a\u064b-\u065f\u0670\u0640])+")
_JOIN = re.compile(r"[ \t -]*")


def _number_word(word: str, *, first: bool) -> tuple[str, int, bool] | None:
    """(kind, value, joined by «و» or "and") of one word, or None when it is no number word."""
    plain = normalize(word)
    candidates = [(plain, False)]
    for lead in _LEAD:
        if plain.startswith(lead) and len(plain) > len(lead) + 1 and (first or lead == "و"):
            candidates.append((plain[len(lead) :], lead.startswith("و")))
    for form, joined in candidates:
        for kind, table in (("unit", _AR_UNITS), ("unit", _EN_UNITS), ("teen_unit", _AR_TEEN_UNITS),
                            ("tens", _AR_TENS), ("tens", _EN_TENS), ("teen", _EN_TEENS),
                            ("hundred", _AR_HUNDREDS), ("thousand", _AR_THOUSANDS)):
            if form in table:
                return kind, table[form], joined
        if form in _AR_TEN:
            return "ten", 10, joined
        if form == "hundred":
            return "hundred", 100, joined
        if form == "thousand":
            return "thousand", 1000, joined
    return None


def _continues(previous: str, kind: str) -> bool:
    """Whether a number word not joined by «و» or "and" continues the number before it: «ثلاثة عشر»،
    «ثلاث مئة»، «خمسة آلاف», "five hundred", "twenty-one", "twenty one"."""
    return kind == "ten" and previous in ("unit", "teen_unit") or kind in ("hundred", "thousand") or (
        kind == "unit" and previous == "tens"
    )


def _read_number(text: str, words: list[re.Match], start: int) -> tuple[int, int, int]:
    """(index after the number's last word, its value, its number of words) from words[start]."""
    total = current = parts = 0
    previous: str | None = None
    after_and = False
    j = start
    while j < len(words):
        word = words[j].group()
        gap = text[words[j - 1].end() : words[j].start()] if j > start else ""
        if j > start and not _JOIN.fullmatch(gap):
            break
        parsed = _number_word(word, first=j == start)
        if parsed is None:
            if normalize(word) == "and" and previous in ("hundred", "thousand") and not after_and:
                after_and = True
                j += 1
                continue
            break
        kind, value, joined = parsed
        if previous is None and normalize(word) == "الاف":
            break  # «آلاف» alone is "thousands", no figure
        if previous is not None and not (joined or after_and or _continues(previous, kind)):
            break
        if kind == "teen_unit" and not (
            j + 1 < len(words) and _JOIN.fullmatch(text[words[j].end() : words[j + 1].start()])
            and normalize(words[j + 1].group()) in _AR_TEN
        ):
            break
        if kind == "hundred" and value == 100 and 0 < current < 10:
            current *= 100
        elif kind == "thousand":
            total += (current or 1) * value if value == 1000 else value
            current = 0
        else:
            current += value
        previous, after_and = kind, False
        parts += 1
        j += 1
    if after_and:
        j -= 1  # "and" followed by no number word is not part of the number
    return j, total + current, parts


def fold_number_words(text: str) -> str:
    """`text` with Arabic and English number words written as digits, everything else unchanged
    (lines and punctuation kept): «واحد وعشرون» and «واحد وعشرين» → 21, «عشرة أسابيع» → «10 أسابيع»,
    «ثلاثمائة وخمسون» → 350, «خمسة آلاف» → 5000, «أحد عشر» → 11, «خمسة وعشرون بالمئة» → «25 بالمئة»
    (المئة with the article is the percent sign, never a hundred), "twenty-one" → 21, "three hundred
    and five" → 305. A lone «واحد»/"one" is left as it is: it is more often a pronoun or «مرة واحدة»
    than a figure."""
    words = list(_NUMBER_WORD.finditer(text))
    out: list[str] = []
    at = i = 0
    while i < len(words):
        end, value, parts = _read_number(text, words, i)
        if parts == 0 or parts == 1 and value == 1:
            i += 1
            continue
        out += [text[at : words[i].start()], str(value)]
        at, i = words[end - 1].end(), end
    out.append(text[at:])
    return "".join(out)
