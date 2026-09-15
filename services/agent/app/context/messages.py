"""Reading history messages for Context Assembly."""

from typing import Any

_ROLE_BY_TYPE = {"human": "user", "user": "user", "ai": "assistant", "assistant": "assistant"}


def role_and_text(message: Any) -> tuple[str, str] | None:
    """Accepts LangGraph messages (BaseMessage) and plain {role, content} dicts.
    A retracted reply is skipped: it never reaches a model again (ADR-0048).
    Only `content` is read, the post-guard text; `shown_answer` is audit metadata."""
    if isinstance(message, dict):
        kind, content, retracted = message.get("role"), message.get("content"), message.get("retracted")
    else:
        kind, content = getattr(message, "type", None), getattr(message, "content", None)
        retracted = (getattr(message, "additional_kwargs", None) or {}).get("retracted")
    if retracted:
        return None
    role = _ROLE_BY_TYPE.get(kind or "")
    if role is None or not isinstance(content, str):
        return None
    return role, content
