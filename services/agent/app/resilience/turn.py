"""The whole-turn deadline and the turn limits (config/turn.yaml; I2, ADR-0255; the PR #12 review,
decisions 1, 2 and 5, ADR-0262 to ADR-0264).

The deadline opens when the turn's request arrives (decision 5). Ownership and the conversation's
lock are taken within `admission_s`; a capability runs under min(its manifest's
limits.timeout_s, what remains − answer_reserve_s), the evidence fetch under
min(evidence_fetch_s, what remains − answer_reserve_s), the answer under min(its ceiling, what
remains − post_answer_reserve_s), and the steps after it (guards, persistence, summary, memory)
inside that reserve (app/resilience/deadline.py). The gateway reads the same file for its relay's
bounds: the agent's response headers within gateway_headers_s, the whole stream within
deadline_s + gateway_margin_s (I3, ADR-0256).

Turn limits (rule 12a): at most `per_user_max` turns of one user at once, `per_tenant_max` of one
tenant (null: the platform capacity, so a single-entity deployment has no tenant restriction of
its own; `per_tenant_overrides` set one tenant's share) and the platform capacity, LOCK_POOL_SIZE,
the turn locks' pool. Past one, the turn is refused at once."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.resilience.limits import Caps, ConcurrencyLimiter


class TurnConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    deadline_s: float = Field(gt=0)
    answer_reserve_s: float = Field(ge=0)
    post_answer_reserve_s: float = Field(default=30.0, ge=0)
    persist_s: float = Field(default=10.0, gt=0)
    evidence_fetch_s: float = Field(gt=0)
    admission_s: float = Field(default=10.0, gt=0)
    gateway_headers_s: float = Field(default=15.0, gt=0)
    gateway_margin_s: float = Field(gt=0)
    per_user_max: int = Field(default=2, ge=1)
    per_tenant_max: int | None = Field(default=None, ge=1)
    per_tenant_overrides: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _nested(self) -> "TurnConfig":
        if self.answer_reserve_s >= self.deadline_s:
            raise ValueError("answer_reserve_s must be shorter than deadline_s")
        if self.post_answer_reserve_s >= self.answer_reserve_s and self.answer_reserve_s > 0:
            raise ValueError("post_answer_reserve_s must be shorter than answer_reserve_s")
        if self.persist_s >= self.post_answer_reserve_s and self.post_answer_reserve_s > 0:
            raise ValueError("persist_s must be shorter than post_answer_reserve_s")
        if any(value < 1 for value in self.per_tenant_overrides.values()):
            raise ValueError("per_tenant_overrides must be at least 1")
        return self

    def limiter(self, platform_capacity: int) -> ConcurrencyLimiter:
        """The turn limits over `platform_capacity` turns at once (LOCK_POOL_SIZE)."""
        return ConcurrencyLimiter(
            Caps(
                per_user=self.per_user_max,
                per_tenant=self.per_tenant_max,
                tenant_overrides=dict(self.per_tenant_overrides),
                platform=platform_capacity,
            )
        )


def load_turn_config(config_dir: Path) -> TurnConfig:
    return TurnConfig.model_validate(yaml.safe_load((config_dir / "turn.yaml").read_text(encoding="utf-8")))
