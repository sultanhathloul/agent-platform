"""sanitize_untrusted (README §10.3): everything a tool returns is data. Before any
POST_TOOL policy and before Context Assembly sees it: scripts, styles and HTML tags
are stripped, entities decoded, strings that look like instructions to the assistant
removed (matched on Arabic-folded text, in English and Arabic), whitespace collapsed
and the size capped. Deterministic and model-free."""

import html
import re
from dataclasses import dataclass

from app.guardrails.config import SanitizeConfig
from app.text.arabic import fold_with_map, span_in_source

_BLOCKS = re.compile(r"<(script|style|noscript|iframe|object|embed)\b.*?</\1\s*>", re.S | re.I)
_COMMENTS = re.compile(r"<!--.*?-->", re.S)
_TAGS = re.compile(r"<[^>]{0,2000}>")
_SPACES = re.compile(r"[ \t\f\v ​‌‍⁠﻿]+")
_LINES = re.compile(r"\n\s*\n\s*(\n\s*)+")
REMOVED = "[removed: instruction-like text]"


@dataclass(frozen=True)
class Sanitized:
    text: str
    removed_instructions: int
    truncated: bool


class Sanitizer:
    def __init__(self, config: SanitizeConfig):
        self._max = config.max_chars
        self._patterns = [re.compile(fold_with_map(p)[0], re.I) for p in config.instruction_patterns]

    def sanitize(self, text: str) -> Sanitized:
        text = _COMMENTS.sub(" ", _BLOCKS.sub(" ", text))
        text = html.unescape(_TAGS.sub(" ", text))
        text, removed = self._strip_instructions(text)
        text = _LINES.sub("\n\n", _SPACES.sub(" ", text)).strip()
        truncated = len(text) > self._max
        return Sanitized(text[: self._max], removed, truncated)

    def _strip_instructions(self, text: str) -> tuple[str, int]:
        folded, mapping = fold_with_map(text)
        regions = []
        for pattern in self._patterns:
            regions += [span_in_source(mapping, *m.span(), len(text)) for m in pattern.finditer(folded)]
        merged: list[list[int]] = []
        for start, end in sorted(regions):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        for start, end in reversed(merged):
            text = text[:start] + REMOVED + text[end:]
        return text, len(merged)
