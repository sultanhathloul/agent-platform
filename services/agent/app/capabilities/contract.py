"""The capability plugin contract (README §18). Every capability, the v1 five
included, is a package under app/capabilities/<slug>/ with a manifest.yaml and
a graph.py whose build() returns a compiled LangGraph subgraph. The subgraph's
state holds `input: CapabilityInput` and returns `output: CapabilityOutput`."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.context.package import Block, BlockName, ContextPackage
from app.identity import Principal
from app.models.gateway import ModelResult
from app.models.routing import CapabilityModelSpec
from app.tools.spec import ToolResult


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------ manifest (§18.2)


class Localized(_Strict):
    ar: str
    en: str


class LocalizedList(_Strict):
    ar: list[str] = []
    en: list[str] = []


class RoutingRule(_Strict):
    # toggled: the user toggled the capability in the UI. keyword: a routing
    # keyword appears in the message. attachments: the turn carries new files.
    # fallback: chosen when no other capability's rule matched.
    when: Literal["toggled", "keyword", "attachments", "fallback"]


class RoutingHints(_Strict):
    description: Localized
    keywords: LocalizedList = LocalizedList()
    rules: list[RoutingRule] = []


class UiSpec(_Strict):
    mode: Literal["toggle", "auto", "button"]
    order: int = 100


class ModelSpec(_Strict):
    min_tier: str
    steps: dict[str, str] = {}
    on_overflow: Literal["compress", "escalate"] | None = None


class ContextSpec(_Strict):
    needs: list[BlockName] = []
    max_input_tokens: int | None = None


class Limits(_Strict):
    max_iterations: int = 1
    timeout_s: float = 30.0


class Manifest(_Strict):
    slug: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    name: Localized
    description: Localized
    icon: str
    enabled_by_default: bool = False
    permission: str
    risk: Literal["read", "write"] = "read"
    ui: UiSpec
    routing_hints: RoutingHints
    model: ModelSpec
    context: ContextSpec = ContextSpec()
    limits: Limits = Limits()
    state_namespace: str

    @model_validator(mode="after")
    def _derived_names(self) -> "Manifest":
        if self.permission != f"cap:{self.slug}":
            raise ValueError(f"permission must be 'cap:{self.slug}' (ADR-0006), got {self.permission!r}")
        if self.state_namespace != f"cap.{self.slug}":
            raise ValueError(f"state_namespace must be 'cap.{self.slug}', got {self.state_namespace!r}")
        return self

    def model_spec(self) -> CapabilityModelSpec:
        return CapabilityModelSpec(
            slug=self.slug,
            min_tier=self.model.min_tier,
            steps=dict(self.model.steps),
            on_overflow=self.model.on_overflow,
            max_input_tokens=self.context.max_input_tokens,
        )


# ------------------------------------------------------------------ runtime (§18.3)


class Source(_Strict):
    id: str
    title: str
    kind: str = Field(description="web | doc | kb | sql")
    url: str | None = None
    kb: str | None = None
    locator: str | None = None


class Artifact(_Strict):
    id: str
    kind: str
    name: str
    data: dict[str, Any] = {}


CapabilityEventType = Literal["tool_start", "tool_end", "citation", "progress"]
CAPABILITY_EVENT_TYPES: frozenset[str] = frozenset(get_args(CapabilityEventType))


class CapabilityEvent(_Strict):
    """The only events a capability may stream or return. `token`, `tier`, `guard`,
    `done`, `error` and `retract` belong to the core (ADR-0047)."""

    type: CapabilityEventType
    data: dict[str, Any]


class CapabilityContractError(TypeError):
    """A capability broke the runtime contract (README §18.3, ADR-0047). Uncaught,
    it ends the turn as `turn_failed`."""


class Usage(_Strict):
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0


class CapabilityInput(_Strict):
    message: str
    context: ContextPackage  # detached: blocks only, no reference to AgentState (ADR-0047)
    principal: Principal
    cap_state: dict[str, Any]  # a copy of this capability's own namespace
    attachments: list[dict[str, Any]] = []
    locale: str


class CapabilityOutput(_Strict):
    findings: str
    sources: list[Source] = []
    artifacts: list[Artifact] = []
    events: list[CapabilityEvent] = []
    cap_state: dict[str, Any]  # required: persisted back into the namespace as returned
    # Non-model cost only (e.g. a paid lookup). Model usage is recorded once, by
    # call_model, so tokens here would be counted twice (ADR-0047).
    cost: Usage = Usage()

    @model_validator(mode="after")
    def _no_model_tokens_in_cost(self) -> "CapabilityOutput":
        if self.cost.tokens_in or self.cost.tokens_out:
            raise ValueError("cost carries non-model cost only; call_model records model tokens (ADR-0047)")
        return self


# ------------------------------------------------------------------ runtime context (§18.3, ADR-0047)


class ModelCaller(Protocol):
    """Calls a model for this capability. The core resolves the tier for (role,
    step) against the manifest, assembles context through `context.needs`, calls
    LiteLLM and records usage. `extra_blocks` are findings the capability produced
    (Block name `findings`); they need `findings` in `context.needs`."""

    async def __call__(
        self, role: str, *, step: str | None = None, extra_blocks: Sequence[Block] = ()
    ) -> ModelResult: ...


class CapabilityBroker(Protocol):
    """The shared tool broker, bound to this turn's principal and event stream."""

    async def execute(self, name: str, args: dict[str, Any]) -> ToolResult: ...


class EventWriter(Protocol):
    """Streams one CapabilityEvent to the client while the capability runs; any
    other value raises CapabilityContractError."""

    def __call__(self, event: CapabilityEvent) -> None: ...


@dataclass(frozen=True)
class RuntimeContext:
    """The LangGraph runtime context of a capability subgraph (`Runtime[RuntimeContext]`).
    This is the whole surface a capability has into the core: its slug, the
    principal, a copy of its own state slice, `call_model`, the broker and the
    event writer. No resolver, guard engine, model config or AgentState (ADR-0047)."""

    slug: str
    principal: Principal
    cap_state: dict[str, Any]
    call_model: ModelCaller
    broker: CapabilityBroker
    stream_writer: EventWriter
