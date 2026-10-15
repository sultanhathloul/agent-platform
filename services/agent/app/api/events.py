"""SSE events streamed by the agent (README §6.3).

This module is the single definition of the event envelope and every event
type. It is exported as JSON Schema to docs/events.schema.json, and the web
app generates its TypeScript types from that file. The gateway forwards the
bytes unchanged.

Wire format, one event per block:

    event: <type>
    id: <seq>
    data: <AgentEvent as JSON>
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------ payloads


class TokenData(_Model):
    text: str


class TierData(_Model):
    role: str
    step: str | None = None
    tier: str
    reason: str


class GuardData(_Model):
    """What a client sees of a guard evaluation: who decided what, never how (ADR-0116). No
    checker explanation, reason, confidence, category, cache status, entities or latency."""

    checkpoint: str
    policy_id: str | None = None
    policy_version: int | None = Field(default=None, description="With policy_id: the version that decided.")
    policy_name: str | None = None
    action: str
    target: str | None = Field(default=None, description="DATA only: answer, memory_candidate or summary.")
    message_ar: str | None = Field(default=None, description="Block only: the policy's own message.")
    message_en: str | None = Field(default=None, description="Block only: the policy's own message.")


class GuardUsage(_Model):
    tokens_in: int
    tokens_out: int


class GuardAuditData(GuardData):
    """A guard evaluation in full, for the gateway's audit record only (ADR-0116). It travels in
    the terminal event's `guard_audit`, which the gateway removes before the event reaches a
    client; a client never receives one."""

    detail: str | None = Field(default=None, description="The checker's explanation.")
    reason: str | None = Field(
        default=None, description="The checker's reason key; `_cached` marks a cached judge verdict."
    )
    reason_args: dict[str, str] = Field(default={}, description="Judge: category and confidence.")
    score: float | None = None
    entities: list[str] = Field(default=[], description="Entity types matched; never the matched text.")
    latency_ms: int | None = None
    usage: GuardUsage | None = Field(
        default=None,
        description="The check's own model calls (the judge's), absent for a cached verdict; never part "
        "of the turn usage a client receives (ADR-0118).",
    )


_GUARD_AUDIT_DESCRIPTION = (
    "Agent to gateway only (ADR-0116): every guard evaluation in full, for the audit record. "
    "The gateway removes this field before relaying the event; a client never receives it."
)


class ToolLabel(_Model):
    ar: str
    en: str


class ToolStartData(_Model):
    call_id: str
    tool: str
    capability: str
    label: ToolLabel | None = Field(
        default=None,
        description="What the client shows while the tool runs, per locale (I13); absent: a generic string.",
    )


class ToolEndData(_Model):
    call_id: str
    tool: str
    capability: str
    status: Literal["ok", "error", "denied", "pending_approval"]
    latency_ms: int


class Citation(_Model):
    id: str
    title: str
    source: str = Field(description="Kind of source, e.g. web, doc, kb, sql.")
    url: str | None = None
    kb: str | None = None
    locator: str | None = Field(default=None, description="Page, section or row range inside the source.")
    document_id: str | None = Field(
        default=None, description="The attachment (or KB document) the answer drew on (ADR-0140)."
    )
    pages: list[int] = Field(
        default=[], description="Pages of that document the answer drew on, ascending (ADR-0140)."
    )


class Usage(_Model):
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0


class StateDigest(_Model):
    turn: int
    capabilities: list[str] = Field(description="Capability slugs that ran this turn, in order.")


class DoneData(_Model):
    answer: str
    citations: list[Citation] = []
    usage: Usage
    guard_events: list[GuardData] = []
    guard_audit: list[GuardAuditData] = Field(default=[], description=_GUARD_AUDIT_DESCRIPTION)
    state_digest: StateDigest
    trace_id: str | None = None


class ErrorData(_Model):
    code: str
    message_ar: str
    message_en: str
    retryable: bool = False
    policy_id: str | None = Field(
        default=None,
        description="policy_id@version whose block ended the turn; its messages are the policy's.",
    )
    guard_events: list[GuardData] = Field(default=[], description="The turn's guard events up to the error.")
    guard_audit: list[GuardAuditData] = Field(default=[], description=_GUARD_AUDIT_DESCRIPTION)


class RetractData(_Model):
    reason: str = Field(description="Error code of the check that withdrew the streamed text (ADR-0039).")


class ProgressData(_Model):
    capability: str
    step: str = Field(description="Machine-readable step name inside the capability, e.g. search or fetch.")
    current: int | None = Field(default=None, ge=0)
    total: int | None = Field(default=None, ge=0)


# ------------------------------------------------------------------ envelope


class _Envelope(_Model):
    request_id: str
    turn_id: str | None
    seq: int = Field(
        description="Monotonic per turn, starting at 0. -1 marks an event injected by the gateway."
    )


class TokenEvent(_Envelope):
    type: Literal["token"]
    data: TokenData


class TierEvent(_Envelope):
    type: Literal["tier"]
    data: TierData


class GuardEvent(_Envelope):
    type: Literal["guard"]
    data: GuardData


class ToolStartEvent(_Envelope):
    type: Literal["tool_start"]
    data: ToolStartData


class ToolEndEvent(_Envelope):
    type: Literal["tool_end"]
    data: ToolEndData


class CitationEvent(_Envelope):
    type: Literal["citation"]
    data: Citation


class DoneEvent(_Envelope):
    type: Literal["done"]
    data: DoneData


class ErrorEvent(_Envelope):
    type: Literal["error"]
    data: ErrorData


class RetractEvent(_Envelope):
    """Withdraws every answer token streamed so far in this turn."""

    type: Literal["retract"]
    data: RetractData


class ProgressEvent(_Envelope):
    """A capability reports how far it got (ADR-0047)."""

    type: Literal["progress"]
    data: ProgressData


AgentEvent = Annotated[
    TokenEvent
    | TierEvent
    | GuardEvent
    | ToolStartEvent
    | ToolEndEvent
    | CitationEvent
    | ProgressEvent
    | DoneEvent
    | ErrorEvent
    | RetractEvent,
    Field(discriminator="type"),
]

EVENT_ADAPTER: TypeAdapter[AgentEvent] = TypeAdapter(AgentEvent)

_PAYLOADS: dict[str, type[_Model]] = {
    "token": TokenData,
    "tier": TierData,
    "guard": GuardData,
    "tool_start": ToolStartData,
    "tool_end": ToolEndData,
    "citation": Citation,
    "progress": ProgressData,
    "done": DoneData,
    "error": ErrorData,
    "retract": RetractData,
}

EventType = Literal[
    "token", "tier", "guard", "tool_start", "tool_end", "citation", "progress", "done", "error", "retract"
]


def validate_payload(type_: EventType, data: dict) -> dict:
    """The payload of an event of `type_`, validated; raises pydantic.ValidationError."""
    return _PAYLOADS[type_].model_validate(data).model_dump()


def build_event(type_: EventType, data: dict | _Model, *, request_id: str, turn_id: str | None, seq: int):
    """Validates a payload against its event type and returns the typed event."""
    payload = data if isinstance(data, _Model) else _PAYLOADS[type_].model_validate(data)
    return EVENT_ADAPTER.validate_python(
        {
            "type": type_,
            "request_id": request_id,
            "turn_id": turn_id,
            "seq": seq,
            "data": payload.model_dump(),
        }
    )


def to_sse(event) -> str:
    """Serializes one event as an SSE block (UTF-8 JSON, no ASCII escaping)."""
    return f"event: {event.type}\nid: {event.seq}\ndata: {event.model_dump_json()}\n\n"
