"""sanitize_untrusted (README §10.3): everything a tool returns is data. Before any
POST_TOOL policy and before Context Assembly sees it: entities are decoded first (HTML
entities, repeatedly, and full-width bracket look-alikes), then scripts, styles and HTML tags
are stripped, so an encoded tag is stripped like a plain one (owner decision S2); any angle
bracket left can no longer open or close a prompt's delimiter (`neutralize_delimiters`); strings
that look like instructions to the assistant are removed (matched on Arabic-folded text, in
English and Arabic), whitespace collapsed and the size capped. Deterministic and model-free.

Neutralizing delimiters is not a policy: it applies to every principal (ADR-0158)."""

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
# Characters that render as angle brackets; folded before tags are stripped.
_BRACKETS = str.maketrans({"＜": "<", "﹤": "<", "＞": ">", "﹥": ">"})
# What is left of an angle bracket after stripping cannot form `<untrusted ...>`, `</untrusted>`
# or any other delimiter of a prompt.
_DELIMITERS = str.maketrans({"<": "‹", ">": "›"})
_DECODE_ROUNDS = 3


def decode(text: str) -> str:
    """HTML entities decoded until nothing changes (at most a few rounds, so `&amp;lt;` is `<`),
    and bracket look-alikes folded to the brackets they render as."""
    for _ in range(_DECODE_ROUNDS):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    return text.translate(_BRACKETS)


def neutralize_delimiters(text: str) -> str:
    return text.translate(_DELIMITERS)


@dataclass(frozen=True)
class Sanitized:
    text: str
    removed_instructions: int
    truncated: bool


class Sanitizer:
    def __init__(self, config: SanitizeConfig):
        self._max = config.max_chars
        self._patterns = [re.compile(fold_with_map(p)[0], re.I) for p in config.instruction_patterns]
        self._markers = config.removed_marker

    def marker(self, locale: str) -> str:
        """The configured marker in the turn's locale (config/messages/sanitize.yaml)."""
        return self._markers.get(locale) or next(iter(self._markers.values()))

    def sanitize(self, text: str, locale: str = "ar") -> Sanitized:
        text = _COMMENTS.sub(" ", _BLOCKS.sub(" ", decode(text)))
        text = neutralize_delimiters(_TAGS.sub(" ", text))
        text, removed = self._strip_instructions(text, self.marker(locale))
        text = _LINES.sub("\n\n", _SPACES.sub(" ", text)).strip()
        truncated = len(text) > self._max
        return Sanitized(text[: self._max], removed, truncated)

    def _strip_instructions(self, text: str, marker: str) -> tuple[str, int]:
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
            text = text[:start] + marker + text[end:]
        return text, len(merged)
