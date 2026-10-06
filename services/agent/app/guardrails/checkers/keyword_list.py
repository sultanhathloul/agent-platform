"""`keyword_list` (README §10.2): allow or deny lists matched word by word after
Arabic normalization (alef forms, taa marbuta and haa, yaa and alef maqsura,
tashkeel and tatweel removed, case folded) with light prefix stripping of
ال، و، ب، ل، ف on both the text's words and the keywords. A keyword of several
words matches consecutive words. `deny` matches when a keyword occurs; `allow`
matches when none does (the text is outside the list)."""

import re

from app.guardrails.checkers.base import CheckContext
from app.guardrails.schema import CheckResult, KeywordListConfig, Span
from app.text.arabic import keyword_stems, normalize

# A word keeps its tashkeel and tatweel, so its offsets cover what the reader sees.
_WORD = re.compile(r"[\wـً-ٰٟ]+")


class KeywordListChecker:
    name = "keyword_list"

    async def check(self, payload: str, config: KeywordListConfig, ctx: CheckContext) -> CheckResult:
        return check_keywords(payload, config)


def check_keywords(text: str, config: KeywordListConfig) -> CheckResult:
    words = [(m.start(), m.end(), keyword_stems(m.group())) for m in _WORD.finditer(text)]
    spans: list[Span] = []
    found: set[str] = set()
    for keyword in config.keywords:
        parts = [keyword_stems(part) for part in normalize(keyword).split()]
        if not parts:
            continue
        for i in range(len(words) - len(parts) + 1):
            if all(words[i + j][2] & parts[j] for j in range(len(parts))):
                spans.append(
                    Span(start=words[i][0], end=words[i + len(parts) - 1][1], entity="KEYWORD", score=1.0)
                )
                found.add(keyword)
    if config.mode == "allow":
        matched = not found
        return CheckResult(
            matched=matched,
            score=1.0 if matched else 0.0,
            explanation="outside the allow list" if matched else f"allowed: {', '.join(sorted(found))}",
        )
    return CheckResult(
        matched=bool(spans),
        spans=spans,
        score=1.0 if spans else 0.0,
        explanation=f"keywords: {', '.join(sorted(found))}" if found else "no keyword",
    )
