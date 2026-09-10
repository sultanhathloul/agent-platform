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


def contains_keyword(text: str, keyword: str) -> bool:
    """Substring match on normalized forms, so Arabic prefixes (ال، و، ب) still match."""
    needle = normalize(keyword)
    return bool(needle) and needle in normalize(text)
