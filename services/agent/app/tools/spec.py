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
class ToolSpec:
    name: str
    description_ar: str
    description_en: str
    input_schema: type[BaseModel]
    risk: Literal["read", "write"]
    required_permission: str
    timeout_s: float
    max_retries: int
    handler: Callable[[BaseModel], Awaitable[ToolOutput]]
