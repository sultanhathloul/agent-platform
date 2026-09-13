"""Token counting for context budgets.

Counts use tiktoken's o200k_base encoding as a model-agnostic estimate; the
served models have their own tokenizers, so budgets keep headroom. Billing-grade counts come from the model's usage in the response.
The encoding file is baked into the image (TIKTOKEN_CACHE_DIR), so runtime
works offline.
"""

from functools import lru_cache
from typing import Protocol

import tiktoken


class TokenCounter(Protocol):
    def count(self, text: str) -> int: ...


class TiktokenCounter:
    def __init__(self, encoding: str = "o200k_base"):
        self._encoding = tiktoken.get_encoding(encoding)

    def count(self, text: str) -> int:
        return len(self._encoding.encode(text, disallowed_special=())) if text else 0


@lru_cache
def default_counter() -> TokenCounter:
    return TiktokenCounter()
