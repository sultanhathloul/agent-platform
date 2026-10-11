"""Tier resolver (README §19.3). Maps (role, step, capabilities) to a tier and
records why on the span as `model.tier_decision`.

The floor (step 1), the context escalation of a NeedsMore (step 3, `escalate`,
ADR-0056) and the cap (step 5) are applied. Router and validation escalation
(steps 2 and 4) join in phase 08 without changing this interface.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from opentelemetry.trace import Span
from pydantic import BaseModel

from app.guardrails.checkpoints import Checkpoint
from app.guardrails.engine import GuardEngine
from app.guardrails.schema import GuardContext, GuardEventRecord
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
    # The MODEL_TIER evaluations behind `cap`; the caller reports them to the turn (C1).
    guard_events: list[GuardEventRecord] = []

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

        # 2, 4. Router and validation escalation: phase 08. 3. Context escalation: `escalate`.
        wanted = floor

        # 5. Cap: caps.default, lowered by the ceiling the gateway derived from the
        #    caller's roles (ADR-0046, ADR-0049), lowered again by every MODEL_TIER policy
        #    that applies (the 7th checkpoint; ceilings only, D2).
        cap, source, events = await self._cap(principal, role, step, wanted, capabilities)
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
            guard_events=events,
        )

    def overflow_action(self, capabilities: Sequence[CapabilityModelSpec]) -> Literal["compress", "escalate"]:
        """What a NeedsMore resolves to (README §19.3 step 3): a capability's manifest
        `on_overflow` wins over `escalation.context_overflow_default`; when several
        capabilities declare one, `escalate` wins, since one of them needs the room."""
        declared = {c.on_overflow for c in capabilities if c.on_overflow}
        if declared:
            return "escalate" if "escalate" in declared else "compress"
        return self.config.escalation.context_overflow_default

    async def escalate(self, decision: TierDecision, *, principal: Principal, reason: str) -> TierDecision:
        """One tier above `decision.final` for `reason`, bounded by the same cap and seen
        by the MODEL_TIER checkpoint. The decision is unchanged at the top tier."""
        rank = self.config.rank(decision.final)
        if rank + 1 >= len(self.config.order):
            return decision.model_copy(update={"guard_events": []})
        wanted = self.config.order[rank + 1]
        cap, source, events = await self._cap(principal, decision.role, decision.step, wanted, ())
        final = self.config.lowest(wanted, cap)
        return decision.model_copy(
            update={
                "escalations": [*decision.escalations, f"{reason}:{wanted}"],
                "cap": cap,
                "final": final,
                "capped": final != wanted,
                "reason": f"{decision.reason}; escalated for {reason}"
                + (f"; capped by {source}" if final != wanted else ""),
                "guard_events": events,
            }
        )

    async def _cap(
        self,
        principal: Principal,
        role: str,
        step: str | None,
        wanted: str,
        capabilities: Sequence[CapabilityModelSpec],
    ) -> tuple[str, str, list[GuardEventRecord]]:
        """The ceiling for this request and what set it: `caps.default` or the principal's
        max_tier (ADR-0049), lowered by the MODEL_TIER policies of the principal's tenant
        and the capabilities the call is made for."""
        ceiling, source = self._ceiling(principal)
        outcome = await self.guard.evaluate(
            Checkpoint.MODEL_TIER,
            {"role": role, "step": step, "tier": wanted, "cap": ceiling},
            principal=principal,
            context=GuardContext(tools=tuple(c.slug for c in capabilities)),
        )
        cap = outcome.payload["cap"]
        if cap != ceiling:
            source = f"policy {outcome.payload.get('capped_by')}"
        return cap, source, list(outcome.events)

    def _ceiling(self, principal: Principal) -> tuple[str, str]:
        """Caps are ceilings: the lower of `caps.default` and `principal.max_tier`. A role
        cap never raises the ceiling above the default (ADR-0049)."""
        default = self.config.caps["default"]
        if principal.max_tier is None or self.config.rank(principal.max_tier) >= self.config.rank(default):
            return default, "caps.default"
        return principal.max_tier, "principal.max_tier"
