"""Context Assembly steps (README §7). The pipeline order comes from
config/context.yaml; each name there maps to a class in STEPS."""

from app.context.selectors.base import MemoryReader, Step, StepDeps
from app.context.selectors.blocks import (
    CapabilityFindings,
    MemoryRetrieval,
    RollingSummary,
    SemanticRetrieval,
    SlidingWindow,
    StateDigest,
    SystemInstructions,
    ToolResults,
    UserMessage,
)
from app.context.selectors.budget_guard import BudgetGuard

STEPS: dict[str, type[Step]] = {
    step.name: step
    for step in (
        SystemInstructions,
        StateDigest,
        RollingSummary,
        SlidingWindow,
        MemoryRetrieval,
        SemanticRetrieval,
        ToolResults,
        CapabilityFindings,
        UserMessage,
        BudgetGuard,
    )
}

__all__ = ["STEPS", "MemoryReader", "Step", "StepDeps"]
