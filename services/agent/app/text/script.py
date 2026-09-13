"""Writing-script checks on generated text.

Deterministic and model-free: `validate` (README §5.2) uses them to reject an
answer written in a script the turn's locale never asks for. Small models drift
into Chinese on Arabic prompts (ADR-0024), which is a failed answer rather than
a style problem.
"""

import re

# CJK unified ideographs and extension A, compatibility ideographs, kana, hangul.
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿가-힯]")


def contains_cjk(text: str) -> bool:
    """True when the text holds a Chinese, Japanese or Korean character."""
    return bool(_CJK.search(text))
