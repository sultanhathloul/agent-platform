"""config/models.yaml: tiers, roles, escalation and caps (README §19.1)."""

from pathlib import Path
from typing import Literal, get_args

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

# The tier names of README §19.1. config/models.yaml must define exactly these, and
# principal.max_tier accepts only these, so the agent API schema lists them (ADR-0049).
TierName = Literal["fast", "standard", "heavy"]
TIER_NAMES: tuple[str, ...] = get_args(TierName)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TierSpec(_Strict):
    alias: str
    window: int
    max_output: int
    cost_weight: float


class Escalation(_Strict):
    complexity_high: str
    risk_high: str
    context_overflow_default: Literal["compress", "escalate"]
    on_validation_failure: str
    max_escalations_per_turn: int


class Retry(_Strict):
    min_remaining_s: float = Field(gt=0)
    backoff_s: tuple[float, float]


class Timeouts(_Strict):
    """Wall-clock bounds on model calls, each cut to what remains of the call around it
    (timeout budget PR, I4, ADR-0257)."""

    roles: dict[str, float]
    embedding: dict[Literal["query", "ingest"], float]
    rerank: float = Field(gt=0)
    retry: Retry

    @model_validator(mode="after")
    def _positive(self) -> "Timeouts":
        for where, value in [*self.roles.items(), *self.embedding.items()]:
            if value <= 0:
                raise ValueError(f"timeouts: {where} must be positive")
        low, high = self.retry.backoff_s
        if not 0 <= low <= high:
            raise ValueError("timeouts.retry.backoff_s: [low, high] with 0 <= low <= high")
        return self


class ModelsConfig(_Strict):
    tiers: dict[str, TierSpec]
    order: list[str]
    roles: dict[str, str]
    temperature: dict[str, float]
    escalation: Escalation
    caps: dict[str, str]
    embedding: dict[str, str]
    # The rerank alias (phase 04, ADR-0134); like an embedding, a rerank is not a chat call.
    rerank: dict[str, str] = {"alias": "rerank"}
    timeouts: Timeouts

    @model_validator(mode="after")
    def _references_resolve(self) -> "ModelsConfig":
        if sorted(self.tiers) != sorted(TIER_NAMES):
            raise ValueError(f"tiers must be exactly {list(TIER_NAMES)} (README §19.1, ADR-0049)")
        if sorted(self.order) != sorted(self.tiers):
            raise ValueError("order must list every tier exactly once")
        if "default" not in self.caps:
            raise ValueError("caps.default is required: it bounds every other cap (ADR-0049)")
        refs = [
            *((f"roles.{k}", v) for k, v in self.roles.items()),
            *((f"caps.{k}", v) for k, v in self.caps.items()),
            ("escalation.complexity_high", self.escalation.complexity_high),
            ("escalation.risk_high", self.escalation.risk_high),
        ]
        for where, tier in refs:
            if tier not in self.tiers:
                raise ValueError(f"{where}: unknown tier {tier!r}")
        # Every role decodes at a declared temperature; no code-side default (ADR-0032).
        if sorted(self.temperature) != sorted(self.roles):
            raise ValueError("temperature must name every role in `roles` exactly once")
        if sorted(self.timeouts.roles) != sorted(self.roles):
            raise ValueError("timeouts.roles must name every role in `roles` exactly once (ADR-0257)")
        if sorted(self.timeouts.embedding) != ["ingest", "query"]:
            raise ValueError("timeouts.embedding must name query and ingest")
        for role, value in self.temperature.items():
            if not 0.0 <= value <= 2.0:
                raise ValueError(f"temperature.{role}: {value} is outside 0.0–2.0")
        return self

    def rank(self, tier: str) -> int:
        return self.order.index(tier)

    def highest(self, *tiers: str) -> str:
        return max(tiers, key=self.rank)

    def lowest(self, *tiers: str) -> str:
        return min(tiers, key=self.rank)


def load_models_config(config_dir: Path) -> ModelsConfig:
    raw = yaml.safe_load((config_dir / "models.yaml").read_text(encoding="utf-8"))
    return ModelsConfig.model_validate(raw)
