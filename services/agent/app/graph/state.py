"""AgentState: the single source of truth for a running conversation (README §5.1).

State is structured data read by code and checkpointed per
thread_id = conversation_id. A field belongs here only if some code reads it
and acts on it. Fields below the §5.1 set are recorded in docs/DECISIONS.md.
"""

from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph.message import add_messages


class Attachment(TypedDict):
    id: str
    name: str
    mime: str
    size: int
    collection: str  # session-scoped vector collection name
    status: Literal["pending", "indexed", "failed"]


class ToolCall(TypedDict):
    id: str
    tool: str
    args: dict
    status: Literal["ok", "error", "denied", "pending_approval"]
    latency_ms: int
    retries: int
    error: str | None


class Decision(TypedDict):
    key: str
    value: str
    reason: str
    turn: int


class AgentState(TypedDict, total=False):
    # identity (set by the gateway, never by the model)
    conversation_id: str
    user_id: str
    tenant_id: str
    permissions: list[str]
    # per-turn
    turn: int
    turn_id: str  # addition: debug/context lookups and event envelopes
    locale: str  # addition: selects prompt locale
    user_message: str
    attachments: list[Attachment]
    selected_kbs: list[str]
    intent: dict  # {capabilities, complexity, risk, decided_by}
    plan: list[str] | None
    iteration: int
    tool_calls: list[ToolCall]
    retrieved: list[dict]
    sql_history: list[dict]
    capability_outputs: list[dict]  # addition: CapabilityOutput per slug, read by the answer node
    draft_answer: str | None
    citations: list[dict]
    guard_events: list[dict]
    reflection: dict | None
    # across turns
    summary: str | None
    decisions: list[Decision]
    open_threads: list[str]
    artifacts: list[dict]
    cap_state: dict[str, dict[str, Any]]  # addition: one isolated slice per manifest.state_namespace
    # accounting
    tokens_in: int
    tokens_out: int
    cost_usd: float
    messages: Annotated[list, add_messages]  # raw history; trimmed by Context Assembly, never sent whole
