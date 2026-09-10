"""config/models.yaml: tiers, roles, escalation and caps (README §19.1)."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator


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


class ModelsConfig(_Strict):
    tiers: dict[str, TierSpec]
    order: list[str]
    roles: dict[str, str]
    escalation: Escalation
    caps: dict[str, str]
    embedding: dict[str, str]

    @model_validator(mode="after")
    def _references_resolve(self) -> "ModelsConfig":
        if sorted(self.order) != sorted(self.tiers):
            raise ValueError("order must list every tier exactly once")
        refs = [
            *((f"roles.{k}", v) for k, v in self.roles.items()),
            *((f"caps.{k}", v) for k, v in self.caps.items()),
            ("escalation.complexity_high", self.escalation.complexity_high),
            ("escalation.risk_high", self.escalation.risk_high),
        ]
        for where, tier in refs:
            if tier not in self.tiers:
                raise ValueError(f"{where}: unknown tier {tier!r}")
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
