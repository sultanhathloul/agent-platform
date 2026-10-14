"""Evidence for the answer (README §7 step 6, ADR-0133): retrieved chunks over their token
budget are compressed one by one, extractively (the sentences sharing the most words
with the question, after Arabic normalization), so every chunk keeps its place, its page
and its share of the budget."""

from collections.abc import Sequence

from app.context.compress import extractive
from app.context.tokens import TokenCounter


def compress_texts(texts: Sequence[str], question: str, max_tokens: int, counter: TokenCounter) -> list[str]:
    total = sum(counter.count(t) for t in texts)
    if total <= max_tokens or not texts:
        return list(texts)
    share = max(max_tokens // len(texts), 1)
    return [extractive(text, question, share, counter) for text in texts]
