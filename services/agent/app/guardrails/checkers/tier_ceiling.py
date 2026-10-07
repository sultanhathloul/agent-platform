"""`tier_ceiling` (D2): a MODEL_TIER policy is a ceiling only. It matches when the
tier requested for a model call is above the policy's `max_tier` (for the model
roles it names, all when none), and the engine then serves the call at the
ceiling. The payload is the resolver's request {role, step, tier, cap}."""

from collections.abc import Sequence

from app.guardrails.checkers.base import CheckContext
from app.guardrails.schema import CheckResult, TierCeilingConfig


class TierCeilingChecker:
    name = "tier_ceiling"

    def __init__(self, order: Sequence[str]):
        self._rank = {tier: rank for rank, tier in enumerate(order)}

    async def check(self, payload: dict, config: TierCeilingConfig, ctx: CheckContext) -> CheckResult:
        if config.max_tier not in self._rank:
            raise ValueError(f"unknown tier {config.max_tier!r}")
        if config.roles is not None and payload["role"] not in config.roles:
            return CheckResult(
                matched=False,
                explanation=f"role {payload['role']} not covered",
                reason="role_not_covered",
                reason_args={"role": payload["role"]},
            )
        above = self._rank[payload["tier"]] > self._rank[config.max_tier]
        return CheckResult(
            matched=above,
            score=1.0 if above else 0.0,
            explanation=f"{payload['tier']} above ceiling {config.max_tier}"
            if above
            else "within the ceiling",
            reason="above_ceiling" if above else "within_ceiling",
            reason_args={"tier": payload["tier"], "ceiling": config.max_tier},
        )
