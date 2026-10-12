"""Token-bounded text reduction for Context Assembly (README §7 steps 6–9).

`truncate` cuts at a character boundary; `extractive` keeps the sentences that
share the most words with the question, in their original order; `extractive_delimited`
does the same inside each delimited data block and never touches a delimiter line
(owner decision S2). All are deterministic and model-free."""

import re
from typing import Literal

from app.context.tokens import TokenCounter
from app.text.arabic import normalize

_SENTENCE_BREAK = re.compile(r"(?<=[.!?؟۔\n])\s+")
_WORD = re.compile(r"\w+")
# A delimiter line of a data block: `<untrusted ...>`, `</untrusted>`, `<user_memory>`, ...
_DELIMITER_LINE = re.compile(r"^\s*</?[a-z_]+(?:\s[^<>\n]*)?>\s*$")


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


def extractive_delimited(text: str, question: str, max_tokens: int, counter: TokenCounter) -> str:
    """`extractive` for a text that holds delimited data blocks: every delimiter line is kept
    as it is, and the text between delimiter lines is compressed piece by piece, each piece to
    its share of what the delimiters leave. A data block always keeps its opening and its
    closing line, so a cut can never leave a block open or close it early."""
    if counter.count(text) <= max_tokens:
        return text
    lines = text.split("\n")
    pieces: list[tuple[bool, str]] = []  # (is a delimiter line, text)
    current: list[str] = []
    for line in lines:
        if _DELIMITER_LINE.match(line):
            if current:
                pieces.append((False, "\n".join(current)))
                current = []
            pieces.append((True, line))
        else:
            current.append(line)
    if current:
        pieces.append((False, "\n".join(current)))
    fixed = sum(counter.count(piece) + 1 for is_delimiter, piece in pieces if is_delimiter)
    bodies = [piece for is_delimiter, piece in pieces if not is_delimiter and piece.strip()]
    share = max((max_tokens - fixed) // max(len(bodies), 1), 0)
    kept = [
        piece if is_delimiter or not piece.strip() else extractive(piece, question, share, counter)
        for is_delimiter, piece in pieces
    ]
    return "\n".join(kept)
