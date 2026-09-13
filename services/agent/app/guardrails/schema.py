"""Guardrail result types shared by the engine and its call sites (README §10.1)."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.guardrails.checkpoints import Checkpoint

Action = Literal["allow", "block", "redact", "rewrite", "warn", "require_approval", "log_only"]


class GuardEventRecord(BaseModel):
    """One policy evaluation, kept in state.guard_events and streamed as a `guard` event."""

    model_config = ConfigDict(extra="forbid")

    checkpoint: Checkpoint
    policy_id: str | None = None
    action: Action
    detail: str | None = None


class GuardOutcome(BaseModel):
    """The engine's verdict for one checkpoint: the resulting action, the
    (possibly redacted or filtered) payload, and every policy event."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    action: Action = "allow"
    payload: Any = None
    events: list[GuardEventRecord] = []

    @property
    def blocked(self) -> bool:
        return self.action == "block"
