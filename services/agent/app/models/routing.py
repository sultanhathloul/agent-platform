"""Tier resolver (README §19.3). Maps (role, step, capabilities) to a tier and
records why on the span as `model.tier_decision`.

Phase 01 applies the floor and the cap. Router, context and validation
escalation (steps 2–4) join in phase 08 without changing this interface.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from opentelemetry.trace import Span
from pydantic import BaseModel

from app.guardrails.checkpoints import Checkpoint
from app.guardrails.engine import GuardEngine
from app.identity import Principal
from app.models.config import ModelsConfig


@dataclass(frozen=True)
class CapabilityModelSpec:
    """The part of a manifest the resolver reads (manifest.model)."""

    slug: str
    min_tier: str
    steps: Mapping[str, str] = field(default_factory=dict)
    on_overflow: str | None = None
    max_input_tokens: int | None = None


class TierDecision(BaseModel):
    role: str
    step: str | None
    requested: str
    floor: str
    escalations: list[str] = []
    cap: str
    final: str
    capped: bool
    reason: str

    def record(self, span: Span) -> None:
        span.set_attribute("model.role", self.role)
        if self.step:
            span.set_attribute("model.step", self.step)
        span.set_attribute("model.tier", self.final)
        span.set_attribute(
            "model.tier_decision",
            json.dumps(
                {
                    "requested": self.requested,
                    "floor": self.floor,
                    "escalations": self.escalations,
                    "cap": self.cap,
                    "final": self.final,
                    "capped": self.capped,
                },
            ),
        )


class TierResolver:
    def __init__(self, config: ModelsConfig, guard: GuardEngine):
        self.config = config
        self.guard = guard

    async def resolve(
        self,
        role: str,
        *,
        principal: Principal,
        step: str | None = None,
        capabilities: Sequence[CapabilityModelSpec] = (),
    ) -> TierDecision:
        """Every call names the principal it runs for; there is no uncapped path (ADR-0049)."""
        if role not in self.config.roles:
            raise KeyError(f"unknown model role {role!r}")
        requested = self.config.roles[role]

        # 1. Floor. A call made on behalf of capabilities takes the highest of
        #    their floors (per-step override first); the role default is only
        #    provisional. Other calls use the role default.
        floors = {c.slug: (c.steps.get(step, c.min_tier) if step else c.min_tier) for c in capabilities}
        if floors:
            floor = self.config.highest(*floors.values())
            reason = "capability floor " + ", ".join(f"{slug}={tier}" for slug, tier in floors.items())
        else:
            floor = requested
            reason = f"role default {role}={requested}"

        # 2–4. Router, context and validation escalation: phase 08.
        wanted = floor

        # 5. Cap: caps.default, lowered by the ceiling the gateway derived from the
        #    caller's roles (ADR-0046, ADR-0049), seen by the MODEL_TIER checkpoint
        #    (7th). Policies may lower it further (phase 03).
        ceiling, source = self._ceiling(principal)
        outcome = await self.guard.evaluate(
            Checkpoint.MODEL_TIER,
            {"role": role, "step": step, "tier": wanted, "cap": ceiling},
            principal=principal,
        )
        cap = outcome.payload["cap"]
        final = self.config.lowest(wanted, cap)
        return TierDecision(
            role=role,
            step=step,
            requested=requested,
            floor=floor,
            cap=cap,
            final=final,
            capped=final != wanted,
            reason=reason + (f"; capped by {source}" if final != wanted else ""),
        )

    def _ceiling(self, principal: Principal) -> tuple[str, str]:
        """Caps are ceilings: the lower of `caps.default` and `principal.max_tier`. A role
        cap never raises the ceiling above the default (ADR-0049)."""
        default = self.config.caps["default"]
        if principal.max_tier is None or self.config.rank(principal.max_tier) >= self.config.rank(default):
            return default, "caps.default"
        return principal.max_tier, "principal.max_tier"
