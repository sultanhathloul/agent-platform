"""The whole-turn deadline (config/turn.yaml; I2, ADR-0255). The runner opens the turn's scope; a
capability runs under min(its manifest's limits.timeout_s, what remains − answer_reserve_s), the
evidence fetch under min(evidence_fetch_s, what remains − answer_reserve_s), and the answer under
what remains (app/resilience/deadline.py). The gateway reads the same file for its relay's bound,
deadline_s + gateway_margin_s (I3, ADR-0256)."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class TurnConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    deadline_s: float = Field(gt=0)
    answer_reserve_s: float = Field(ge=0)
    evidence_fetch_s: float = Field(gt=0)
    gateway_margin_s: float = Field(gt=0)

    @model_validator(mode="after")
    def _nested(self) -> "TurnConfig":
        if self.answer_reserve_s >= self.deadline_s:
            raise ValueError("answer_reserve_s must be shorter than deadline_s")
        return self


def load_turn_config(config_dir: Path) -> TurnConfig:
    return TurnConfig.model_validate(yaml.safe_load((config_dir / "turn.yaml").read_text(encoding="utf-8")))
