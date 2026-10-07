"""`regex` (README §10.2): user-supplied patterns. A pattern's named groups become
spans typed by the group name; a pattern without named groups yields one span per
match, typed by the pattern's name. With `fold` (the default) text and pattern are
matched in folded form (app/text/arabic.py fold_with_map): Arabic-Indic digits as
ASCII, tashkeel and tatweel removed, alef, alef maqsura and taa marbuta folded, so
`٠٥٥١٢٣٤٥٦٧` and `تَجاهَل` match patterns written as `05…` and `تجاهل`."""

import re
from functools import lru_cache

from app.guardrails.checkers.base import CheckContext
from app.guardrails.schema import CheckResult, RegexConfig, Span
from app.text.arabic import fold_with_map, span_in_source


@lru_cache(maxsize=512)
def _compiled(pattern: str, ignore_case: bool, fold: bool) -> re.Pattern:
    if fold:
        pattern, _ = fold_with_map(pattern)
    return re.compile(pattern, re.IGNORECASE if ignore_case else 0)


class RegexChecker:
    name = "regex"

    async def check(self, payload: str, config: RegexConfig, ctx: CheckContext) -> CheckResult:
        return check_regex(payload, config)


def check_regex(text: str, config: RegexConfig) -> CheckResult:
    folded, mapping = fold_with_map(text) if config.fold else (text, list(range(len(text))))
    spans: list[Span] = []
    for item in config.patterns:
        for match in _compiled(item.pattern, item.ignore_case, config.fold).finditer(folded):
            named = {name: match.span(name) for name, value in match.groupdict().items() if value}
            regions = named.items() if named else [(item.name, match.span())]
            for entity, (start, end) in regions:
                if end <= start:
                    continue
                source = span_in_source(mapping, start, end, len(text))
                spans.append(Span(start=source[0], end=source[1], entity=entity, score=1.0))
    names = sorted({span.entity for span in spans})
    return CheckResult(
        matched=bool(spans),
        spans=spans,
        score=1.0 if spans else 0.0,
        explanation=f"matched {', '.join(names)}" if spans else "no pattern matched",
        reason="patterns" if spans else "no_pattern",
        reason_args={"items": "\n".join(names)} if spans else {},
    )
