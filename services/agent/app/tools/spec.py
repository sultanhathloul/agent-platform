"""Tool specification and normalized result (README §9)."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel


# The source fields a person reads: POST_TOOL sanitizes and checks them like `content` (S2).
SOURCE_TEXT_FIELDS = ("title", "locator")


class ToolOutput(BaseModel):
    """What a tool handler returns; the broker wraps it into a ToolResult."""

    content: str
    sources: list[dict[str, Any]] = []

    def text_fields(self) -> list[tuple[int, str]]:
        """(source index, key) of every readable source field that holds text."""
        return [
            (index, key)
            for index, source in enumerate(self.sources)
            for key in SOURCE_TEXT_FIELDS
            if isinstance(source.get(key), str) and source[key]
        ]

    def field_texts(self) -> list[str]:
        return [self.sources[index][key] for index, key in self.text_fields()]

    def with_field_texts(self, texts: list[str]) -> "ToolOutput":
        sources = [dict(source) for source in self.sources]
        for (index, key), text in zip(self.text_fields(), texts, strict=True):
            sources[index][key] = text
        return self.model_copy(update={"sources": sources})


class ToolResult(BaseModel):
    status: Literal["ok", "error", "denied", "pending_approval"]
    content: str = ""
    sources: list[dict[str, Any]] = []
    latency_ms: int = 0
    retries: int = 0
    error: str | None = None


@dataclass(frozen=True)
class ToolScope:
    """Whose data a tool call may touch. The broker builds it from the verified principal and
    the conversation in state; it is never an argument a capability or a model fills (S1)."""

    tenant_id: str
    user_id: str
    conversation_id: str


@dataclass(frozen=True)
class ToolContext:
    """What a handler receives beside its arguments: the scope and the document backend the
    calling capability's RuntimeContext holds (C3, C6). No module global reaches a handler."""

    scope: ToolScope
    documents: Any = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description_ar: str
    description_en: str
    input_schema: type[BaseModel]
    risk: Literal["read", "write"]
    required_permission: str
    timeout_s: float
    max_retries: int
    handler: Callable[[BaseModel, ToolContext], Awaitable[ToolOutput]]
