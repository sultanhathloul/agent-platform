"""What graph nodes can reach: long-lived services built at startup, and the
per-turn runtime context passed to LangGraph (`context_schema`)."""

from dataclasses import dataclass, field
from typing import Any

from app.api.messages import ErrorMessages
from app.api.ownership import ConversationOwners
from app.capabilities.registry import CapabilityRegistry
from app.context.assembler import ContextAssembler
from app.guardrails.engine import GuardEngine
from app.identity import Principal
from app.models.config import ModelsConfig
from app.models.gateway import ModelGateway
from app.models.prompts import PromptLibrary
from app.models.routing import TierResolver
from app.settings import Settings
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
    guard: GuardEngine
    registry: CapabilityRegistry
    tools: ToolRegistry
    broker: ToolBroker
    messages: ErrorMessages
    owners: ConversationOwners
    graph: Any  # compiled main graph


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
