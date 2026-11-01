"""A conversation's history as the client sees it (ADR-0192, ADR-0193), read from the
messages of the checkpointed thread. Only `content` leaves, the post-guard text every later
model call reads; `shown_answer` and `retract_reason` stay audit metadata, and a retracted
exchange is left out as a pair, as Context Assembly leaves it out (ADR-0076)."""

from collections.abc import Sequence
from typing import Any

from app.context.messages import visible


def _kwargs(message: Any) -> dict:
    if isinstance(message, dict):
        return message
    return getattr(message, "additional_kwargs", None) or {}


def history(messages: Sequence[Any]) -> list[dict]:
    """Each shown message: role and text, the attachment ids sent with a question, and a
    reply's turn id and citations. Messages persisted before ADR-0193 have no ids or citations."""
    shown: list[dict] = []
    for message, parsed in zip(messages, visible(messages), strict=True):
        if parsed is None:
            continue
        role, text = parsed
        kwargs = _kwargs(message)
        entry: dict = {"role": role, "text": text}
        if role == "user":
            entry["attachment_ids"] = [i for i in kwargs.get("attachment_ids") or [] if isinstance(i, str)]
        else:
            entry["turn_id"] = kwargs.get("turn_id")
            entry["citations"] = [c for c in kwargs.get("citations") or [] if isinstance(c, dict)]
            artifact = kwargs.get("artifact")
            if isinstance(artifact, dict) and isinstance(artifact.get("id"), str):
                entry["artifact"] = {k: artifact.get(k) for k in ("id", "kind", "title", "version")}
            skill = kwargs.get("skill")
            if isinstance(skill, dict) and isinstance(skill.get("slug"), str):
                entry["skill"] = skill["slug"]
        shown.append(entry)
    return shown


def first_question(messages: Sequence[Any]) -> str | None:
    """The first question still shown, which names the conversation; None without one."""
    return next((m["text"] for m in history(messages) if m["role"] == "user"), None)
