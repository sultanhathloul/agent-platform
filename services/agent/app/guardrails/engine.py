"""Guardrail policy engine (README §10).

Phase 01 ships the checkpoints as hook points with no policies loaded, so
every evaluation allows the payload unchanged (ADR-0009). Phase 03 adds the
policy store, scopes and checkers behind this same `evaluate` call.
"""

from typing import Any

from app.guardrails.checkpoints import Checkpoint
from app.guardrails.schema import GuardOutcome
from app.identity import Principal
from app.observability.otel import tracer


class GuardEngine:
    async def evaluate(
        self,
        checkpoint: Checkpoint,
        payload: Any,
        *,
        principal: Principal | None = None,
        scope: str | None = None,
    ) -> GuardOutcome:
        with tracer.start_as_current_span("guard.check") as span:
            span.set_attribute("guard.checkpoint", checkpoint.value)
            span.set_attribute("guard.policies_evaluated", 0)
            span.set_attribute("guard.action", "allow")
            if scope:
                span.set_attribute("guard.scope", scope)
            return GuardOutcome(action="allow", payload=payload)
