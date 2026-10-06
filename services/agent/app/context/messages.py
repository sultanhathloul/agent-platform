"""Reading history messages for Context Assembly."""

from collections.abc import Sequence
from typing import Any

_ROLE_BY_TYPE = {"human": "user", "user": "user", "ai": "assistant", "assistant": "assistant"}


def _fields(message: Any) -> tuple[Any, Any, Any]:
    if isinstance(message, dict):
        return message.get("role"), message.get("content"), message.get("retracted")
    retracted = (getattr(message, "additional_kwargs", None) or {}).get("retracted")
    return getattr(message, "type", None), getattr(message, "content", None), retracted


def role_and_text(message: Any) -> tuple[str, str] | None:
    """Accepts LangGraph messages (BaseMessage) and plain {role, content} dicts.
    A retracted reply is skipped: it never reaches a model again (ADR-0048).
    Only `content` is read, the post-guard text; `shown_answer` is audit metadata."""
    kind, content, retracted = _fields(message)
    if retracted:
        return None
    role = _ROLE_BY_TYPE.get(kind or "")
    if role is None or not isinstance(content, str):
        return None
    return role, content


def visible(messages: Sequence[Any]) -> list[tuple[str, str] | None]:
    """`role_and_text` of each message, aligned with `messages`. A retracted reply
    removes its exchange as a pair: the user message right before it, which
    persist_state stored with it, is None too (ADR-0076)."""
    parsed = [role_and_text(m) for m in messages]
    for i, message in enumerate(messages):
        if i and _fields(message)[2] and parsed[i - 1] is not None and parsed[i - 1][0] == "user":
            parsed[i - 1] = None
    return parsed
