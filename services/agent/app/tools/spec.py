"""Tool specification and normalized result (README §9)."""

import copy
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

# The source fields a person reads: POST_TOOL sanitizes and checks them like `content` (S2).
SOURCE_TEXT_FIELDS = ("title", "locator")


Path = tuple[str | int, ...]


def _string_leaves(value: Any, path: Path = ()) -> list[Path]:
    if isinstance(value, str):
        return [path] if value else []
    if isinstance(value, dict):
        return [p for key, item in value.items() for p in _string_leaves(item, (*path, key))]
    if isinstance(value, list):
        return [p for index, item in enumerate(value) for p in _string_leaves(item, (*path, index))]
    return []


def _get(value: Any, path: Path) -> Any:
    for step in path:
        value = value[step]
    return value


def _set(value: Any, path: Path, text: str) -> None:
    for step in path[:-1]:
        value = value[step]
    value[path[-1]] = text


class ToolOutput(BaseModel):
    """What a tool handler returns; the broker wraps it into a ToolResult. `data` is a structured
    result (an MCP tool's structured content, I7): POST_TOOL sanitizes and checks every string
    in it like `content`, so it keeps its shape (ADR-0208)."""

    content: str
    sources: list[dict[str, Any]] = []
    data: Any = None
    # Content-free facts about the call for its span and audit entry (G8): server, sizes, the
    # HMAC of the arguments. Never text; POST_TOOL does not read it.
    observed: dict[str, str | int | bool] = {}

    def text_fields(self) -> list[Path]:
        """The path of every readable text beside `content`: each source field a person reads,
        then each string leaf of `data`."""
        paths: list[Path] = [
            ("sources", index, key)
            for index, source in enumerate(self.sources)
            for key in SOURCE_TEXT_FIELDS
            if isinstance(source.get(key), str) and source[key]
        ]
        return paths + [("data", *leaf) for leaf in _string_leaves(self.data)]

    def field_texts(self) -> list[str]:
        root = {"sources": self.sources, "data": self.data}
        return [_get(root, path) for path in self.text_fields()]

    def with_field_texts(self, texts: list[str]) -> "ToolOutput":
        root = copy.deepcopy({"sources": self.sources, "data": self.data})
        for path, text in zip(self.text_fields(), texts, strict=True):
            _set(root, path, text)
        return self.model_copy(update={"sources": root["sources"], "data": root["data"]})


class ToolResult(BaseModel):
    status: Literal["ok", "error", "denied", "pending_approval"]
    content: str = ""
    sources: list[dict[str, Any]] = []
    data: Any = None
    latency_ms: int = 0
    retries: int = 0
    error: str | None = None


class ToolFailure(Exception):
    """A tool that could not answer, by a content-free code the capability can state (I8):
    `tool_unavailable`, `tool_timeout`, `tool_result_too_large`, `tool_error`. The broker reports
    the code and nothing else, and does not retry it."""

    def __init__(self, code: str, observed: dict[str, str | int | bool] | None = None):
        super().__init__(code)
        self.code = code
        self.observed = observed or {}


@dataclass(frozen=True)
class ToolScope:
    """Whose data a tool call may touch. The broker builds it from the verified principal and
    the turn: the tenant, the user and the document collections the calling capability may
    read this turn (I1, ADR-0202). It is never an argument a capability or a model fills (S1)."""

    tenant_id: str
    user_id: str
    collections: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolContext:
    """What a handler receives beside its arguments: the scope and the document backend the
    calling capability's configuration names (C3, C6, I1). No module global reaches a handler."""

    scope: ToolScope
    documents: Any = None


class Label(BaseModel):
    """What the user reads while a tool runs (I13): one short phrase per locale."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ar: str
    en: str


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description_ar: str
    description_en: str
    input_schema: type[BaseModel]
    risk: Literal["read", "write"]
    # None: no permission of its own; the calling capability's declaration and permission decide
    # (an MCP tool, ADR-0205).
    required_permission: str | None
    timeout_s: float
    max_retries: int
    handler: Callable[[BaseModel, ToolContext], Awaitable[ToolOutput]]
    # Streamed in tool_start for the client to show (I13); None leaves it to a generic string.
    label: Label | None = None
    # A tool of an external MCP server (I16, ADR-0217): refused unless an enabled PRE_TOOL policy
    # is scoped to it or to its server.
    external: bool = False
