"""Context budget and the NeedsMore signal (README §7 step 9, §19.3)."""

from pydantic import BaseModel

from app.context.package import ContextPackage
from app.models.config import TierSpec


class Budget(BaseModel):
    tier: str
    window: int
    reserved_output: int
    reserved_headroom: int
    max_input: int | None = None

    @classmethod
    def for_tier(cls, tier: str, spec: TierSpec, *, headroom: int, max_input: int | None = None) -> "Budget":
        return cls(
            tier=tier,
            window=spec.window,
            reserved_output=spec.max_output,
            reserved_headroom=headroom,
            max_input=max_input,
        )

    @property
    def limit(self) -> int:
        """context_budget = tier_window - reserved_output - reserved_loop_headroom, capped by max_input."""
        limit = self.window - self.reserved_output - self.reserved_headroom
        return min(limit, self.max_input) if self.max_input else limit


class NeedsMore(BaseModel):
    """Required blocks do not fit. The tier resolver decides whether to compress
    harder or escalate (phase 02 implements the decision)."""

    deficit_tokens: int
    package: ContextPackage
