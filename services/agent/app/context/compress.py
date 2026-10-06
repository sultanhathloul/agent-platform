"""Token-bounded text reduction for Context Assembly (README §7 steps 6–9).

`truncate` cuts at a character boundary; `extractive` keeps the sentences that
share the most words with the question, in their original order. Both are
deterministic and model-free."""

import re
from typing import Literal

from app.context.tokens import TokenCounter
from app.text.arabic import normalize

_SENTENCE_BREAK = re.compile(r"(?<=[.!?؟۔\n])\s+")
_WORD = re.compile(r"\w+")


def truncate(
    text: str, max_tokens: int, counter: TokenCounter, *, keep: Literal["start", "end"] = "start"
) -> str:
    """The longest prefix (`start`) or suffix (`end`) of `text` within max_tokens."""
    if max_tokens <= 0:
        return ""
    if counter.count(text) <= max_tokens:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        part = text[:middle] if keep == "start" else text[len(text) - middle :]
        if counter.count(part) <= max_tokens:
            low = middle
        else:
            high = middle - 1
    return text[:low] if keep == "start" else text[len(text) - low :]


def _words(text: str) -> set[str]:
    return set(_WORD.findall(normalize(text)))


def extractive(text: str, question: str, max_tokens: int, counter: TokenCounter) -> str:
    """Keeps the sentences most relevant to `question` (shared words after Arabic
    normalization) within max_tokens, in their original order."""
    if counter.count(text) <= max_tokens:
        return text
    sentences = [s for s in _SENTENCE_BREAK.split(text) if s.strip()]
    wanted = _words(question)
    ranked = sorted(range(len(sentences)), key=lambda i: (-len(wanted & _words(sentences[i])), i))
    kept: list[int] = []
    used = 0
    for index in ranked:
        cost = counter.count(sentences[index]) + 1
        if used + cost <= max_tokens:
            kept.append(index)
            used += cost
    joined = " ".join(sentences[i] for i in sorted(kept))
    return truncate(joined or text, max_tokens, counter)
