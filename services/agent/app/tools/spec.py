"""Tool specification and normalized result (README §9)."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel


class ToolOutput(BaseModel):
    """What a tool handler returns; the broker wraps it into a ToolResult."""

    content: str
    sources: list[dict[str, Any]] = []


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
