"""What graph nodes can reach: long-lived services built at startup, and the
per-turn runtime context passed to LangGraph (`context_schema`)."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.api.locks import TurnLocks
from app.api.messages import ErrorMessages
from app.api.ownership import ConversationOwners
from app.attachments.service import AttachmentService
from app.capabilities.registry import CapabilityRegistry
from app.context.assembler import ContextAssembler
from app.context.log import ContextLog
from app.db import Database
from app.graph.router_config import RouterConfig
from app.guardrails.config import GuardrailsConfig
from app.guardrails.engine import GuardEngine
from app.guardrails.schema import GuardEventRecord
from app.guardrails.store import PolicyStore
from app.identity import Principal
from app.memory.service import MemoryService
from app.models.config import ModelsConfig
from app.models.embeddings import EmbeddingGateway
from app.models.gateway import ModelGateway
from app.models.prompts import PromptLibrary
from app.models.routing import TierResolver
from app.rag.backend import DocumentBackend
from app.rag.citations import AttributionRule
from app.resilience.limits import ConcurrencyLimiter
from app.resilience.turn import TurnConfig
from app.settings import Settings
from app.skills.registry import SkillRegistry
from app.text.script import ValidationConfig
from app.tools.broker import ToolBroker
from app.tools.registry import ToolRegistry


@dataclass(frozen=True)
class Services:
    settings: Settings
    models_config: ModelsConfig
    prompts: PromptLibrary
    assembler: ContextAssembler
    resolver: TierResolver
    gateway: ModelGateway
    embeddings: EmbeddingGateway
    guard: GuardEngine
    registry: CapabilityRegistry
    skills: SkillRegistry  # skills/<slug>/ (phase 06, ADR-0295), in memory
    tools: ToolRegistry
    broker: ToolBroker
    messages: ErrorMessages
    validation: ValidationConfig
    db: Database
    owners: ConversationOwners
    memory: MemoryService
    context_log: ContextLog
    policies: PolicyStore
    guard_config: GuardrailsConfig
    locks: TurnLocks
    attachments: AttachmentService
    documents: DocumentBackend  # the one uploads are registered with (I11)
    citations: AttributionRule
    router: RouterConfig
    turn: TurnConfig  # the whole-turn deadline and its reserves (config/turn.yaml, I2, ADR-0255)
    # Turns in flight per user, per tenant and platform-wide (config/turn.yaml, ADR-0262).
    turn_limits: ConcurrencyLimiter
    # The platform settings in force: the files' values with the administrator's overrides
    # (owner decision 2026-10-22, ADR-0276); every request pins the snapshot current at its start.
    platform: Any = None  # app.platform_settings.store.SettingsStore
    redis: Any = None  # redis.asyncio client, or None without REDIS_URL
    closers: tuple = ()  # async close() callables run by stop_services
    # The compiled main graph, set by app.main.start_services once the database is open:
    # the Postgres checkpointer binds to the running event loop (ADR-0055).
    graph: Any = None
    # Every DocumentBackend by name (I1, ADR-0202); a capability's config/<slug>.yaml `backend`
    # names the one its tools use, `documents` otherwise.
    backends: Mapping[str, DocumentBackend] = field(default_factory=dict)
    # Knowledge bases (phase 08): app.kb.service.KbService, or None on a platform without them.
    kbs: Any = None
    # Background work start_services begins and stop_services cancels (e.g. owed KB purges).
    tasks: list = field(default_factory=list)
    # Interactive reports (reports PR): app.artifacts.config.Artifacts and app.artifacts.store.ReportStore.
    artifacts: Any = None
    reports: Any = None

    def backend_name(self, slug: str) -> str | None:
        name = self.registry.get(slug).config.get("backend")
        return str(name) if name else getattr(self.documents, "name", None)


@dataclass
class TurnUsage:
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0

    def add(self, tokens_in: int, tokens_out: int, cost_usd: float = 0.0) -> None:
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        self.cost_usd += cost_usd


@dataclass
class TurnContext:
    services: Services
    principal: Principal
    request_id: str
    turn_id: str
    usage: TurnUsage = field(default_factory=TurnUsage)
    conversation_id: str = ""
    escalations: int = 0  # tier escalations so far this turn (README §19.1 max_escalations_per_turn)
    timings: dict[str, int] = field(default_factory=dict)  # milliseconds, set on the turn span by the runner
    # Every guard evaluation of the turn, in order: the `guard` events, the done/error
    # event's guard_events, the audit delta and the matches-per-day table (C1).
    guard_log: list[GuardEventRecord] = field(default_factory=list)
    # The run's custom stream writer, set by the first node, so checkpoints outside a
    # node's own scope (broker, tier resolver, judge) stream their `guard` events too.
    writer: Callable[[dict], None] | None = None
    # policy_id@version of a fail-closed tool policy that could not run (D8).
    guard_unavailable: str | None = None
    # This turn's chunk texts by reference, fetched for the answer and checked at POST_TOOL;
    # page attribution reads them. Never written to state (owner decision S4).
    evidence: dict[str, str] = field(default_factory=dict)
    # Every tool call of the turn, content-free, for the audit record (G8): the terminal event's
    # tool_audit, which the gateway strips before relaying.
    tool_audit: list[dict] = field(default_factory=list)
    # The answer's last token was sent (streaming: the answer node's end; buffered: release_answer):
    # from then on the turn never ends turn_timeout (ADR-0263).
    answer_sent: bool = False
    # The turn's deadline profile, chosen at admission (config/turn.yaml profiles; reports PR, ADR-0379):
    # "report" when the turn may write a report, None otherwise.
    profile: str | None = None

    def emit(self, type_: str, data: dict) -> None:
        if self.writer is not None:
            self.writer({"type": type_, "data": data})

    def report_guard(self, events: Sequence[GuardEventRecord]) -> None:
        """Records evaluations; those that did something (not `allow`) stream as `guard` events,
        with the client fields only: no checker detail, score or latency (ADR-0116). An audit-only
        record is never streamed (ADR-0118)."""
        self.guard_log.extend(events)
        for event in events:
            if event.action != "allow" and not event.audit_only:
                self.emit("guard", event.client_view())

    def guard_records(self) -> list[dict]:
        """state.guard_events: every evaluation so far this turn."""
        return [event.model_dump(mode="json") for event in self.guard_log]
