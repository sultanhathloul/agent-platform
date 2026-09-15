"""Rolling summary policy (README §7 step 4, ADR-0057): decides which messages are
folded into `state.summary`. The model call runs in the assemble_context node,
through Context Assembly and the `summarizer` role.

`summary_cursor` counts the messages from the start of the history that the
summary already covers; the sliding window reads only messages after it."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.context.messages import role_and_text
from app.context.tokens import TokenCounter


@dataclass(frozen=True)
class SummaryPlan:
    history_tokens: int  # the summary plus the unsummarized, unretracted messages
    history_messages: int
    fold: list[Any]  # messages to fold into the summary now; empty when there is nothing to fold
    cursor_after: int


def plan_summary(
    messages: Sequence[Any],
    *,
    cursor: int,
    summary: str | None,
    window_messages: int,
    trigger_tokens: int,
    counter: TokenCounter,
) -> SummaryPlan:
    """Past `trigger_tokens`, every message older than the window and not yet in the
    summary is folded. Once a summary exists, each later turn folds the messages
    that left the window, so no message falls between the summary and the window.
    Retracted messages are never folded (ADR-0048)."""
    unsummarized = [parsed[1] for m in messages[cursor:] if (parsed := role_and_text(m))]
    tokens = counter.count(summary or "") + sum(counter.count(text) for text in unsummarized)
    due = bool(summary) or tokens > trigger_tokens
    fold_until = max(cursor, len(messages) - window_messages)
    if not due or fold_until == cursor:
        return SummaryPlan(tokens, len(unsummarized), [], cursor)
    fold = [m for m in messages[cursor:fold_until] if role_and_text(m)]
    return SummaryPlan(tokens, len(unsummarized), fold, fold_until)
