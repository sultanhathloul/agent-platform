"""Long-conversation recall (report library PR, owner decision 5, ADR-0434): `conversation_recall(query)`
searches the conversation's whole stored history and returns the matching earlier messages verbatim, so a
detail the rolling summary lost (a pasted table, a figure from an early turn) can be recovered.

The history is the turn's own checkpointed `messages`, which the summary never trims (ADR-0057): the core
binds the facade to that list, so the tool can read no other conversation, and no argument names one.
Retracted exchanges are left out, as Context Assembly leaves them out (ADR-0076); the messages the turn's
window already holds are skipped (config/library.yaml recall.skip_recent).

Scoring is linear and capped (CLAUDE.md rule 12d): at most `max_messages` messages, the newest, and
`max_scan_chars` characters are read; each message's words are search stems (app/text/arabic.py), scored by
the share of the query's stems it holds, weighted by their rarity in the conversation. A query that names a
table or a list also ranks the messages that hold one (several lines with tabs, pipes or several cells;
lines that start with a bullet or a number); a query that names the start of the conversation ranks
earlier messages first among equals. At most `max_results` messages return, each cut to `message_chars`
and all of them to `max_chars`, with the turn they came from."""

import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.artifacts.library import RecallConfig
from app.context.messages import visible
from app.text.arabic import search_stem, search_terms

_TABLE_LINE = re.compile(r"\t|\|.*\||(?:\S+\s{2,}){2,}\S")
_LIST_LINE = re.compile(r"^\s*(?:[-*•·]|\d+[.)-]|[٠-٩]+[.)-])\s+")


@dataclass(frozen=True)
class Recalled:
    index: int  # the message's position in the conversation, from 0
    exchange: int  # the exchange it belongs to, from 1
    role: str
    text: str
    score: float
    cut: bool = False


def _shape(text: str) -> tuple[bool, bool]:
    """(holds a table, holds a list): at least three such lines."""
    lines = text.splitlines()
    table = sum(1 for line in lines if _TABLE_LINE.search(line)) >= 3
    listed = sum(1 for line in lines if _LIST_LINE.match(line)) >= 3
    return table, listed


def _stems(words: Sequence[str]) -> set[str]:
    return {search_stem(w) for w in words}


def recall(messages: Sequence[Any], query: str, config: RecallConfig) -> list[Recalled]:
    shown = visible(messages)
    # The newest `max_messages`, without the window's; positions stay the conversation's own.
    end = max(len(shown) - config.skip_recent, 0)
    start = max(end - config.max_messages, 0)
    candidates: list[tuple[int, str, str]] = []
    budget = config.max_scan_chars
    for index in range(end - 1, start - 1, -1):  # newest first, so the cap keeps the newest
        parsed = shown[index]
        if parsed is None:
            continue
        role, text = parsed
        if budget <= 0:
            break
        text = text[:budget]
        budget -= len(text)
        candidates.append((index, role, text))
    if not candidates:
        return []
    stop = _stems(config.stopwords.all())
    query_terms = [t for t in dict.fromkeys(search_terms(query)) if t not in stop and len(t) >= 2]
    wants_table = bool(_stems(config.table_words) & set(search_terms(query)))
    wants_list = bool(_stems(config.list_words) & set(search_terms(query)))
    wants_early = bool(_stems(config.early_words) & set(search_terms(query)))

    terms_by_message: dict[int, set[str]] = {}
    df: Counter[str] = Counter()
    for index, _, text in candidates:
        terms = set(search_terms(text))
        terms_by_message[index] = terms
        df.update(t for t in query_terms if t in terms)
    n = len(candidates)
    weights = {t: math.log(1 + n / (1 + df[t])) for t in query_terms}
    total = sum(weights.values()) or 1.0

    scored: list[Recalled] = []
    for index, role, text in candidates:
        terms = terms_by_message[index]
        score = sum(w for t, w in weights.items() if t in terms) / total
        table, listed = _shape(text)
        if wants_table and table:
            score += 0.5
        if wants_list and listed:
            score += 0.5
        if score <= 0:
            continue
        if role == "user":
            score += 0.05  # what the user wrote first: a pasted table or figure is theirs
        scored.append(Recalled(index=index, exchange=index // 2 + 1, role=role, text=text, score=round(score, 4)))
    if wants_early:
        scored.sort(key=lambda r: (-r.score, r.index))
    else:
        scored.sort(key=lambda r: (-r.score, -r.index))
    out: list[Recalled] = []
    left = config.max_chars
    for item in scored[: config.max_results]:
        if left <= 0:
            break
        limit = min(config.message_chars, left)
        text = item.text if len(item.text) <= limit else item.text[:limit]
        left -= len(text)
        out.append(Recalled(item.index, item.exchange, item.role, text, item.score, cut=len(text) < len(item.text)))
    out.sort(key=lambda r: r.index)
    return out


class HistoryFacade:
    """The one conversation's history a recall tool may read, bound by the core to the turn's own checkpointed
    messages (the conversation's owner was checked at admission). No conversation id, user or store reaches
    the tool."""

    def __init__(self, messages: Sequence[Any], config: RecallConfig):
        self._messages = list(messages)
        self._config = config

    def search(self, query: str) -> list[Recalled]:
        return recall(self._messages, query, self._config)
