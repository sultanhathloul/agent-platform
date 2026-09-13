"""Context Assembly (README §7). Runs before every model call and produces a
ContextPackage with per-block token accounting, logged on the
`context.assemble` span.

Phase 01 ships the minimal pipeline behind the full interface (ADR-0009);
phase 02 adds summary, memory and the NeedsMore → compress/escalate decision.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from app.context.budget import Budget, NeedsMore
from app.context.package import AssemblyRequest, BlockName, ContextPackage
from app.context.selectors import STEPS, StepDeps
from app.context.tokens import TokenCounter
from app.models.config import TierSpec
from app.models.prompts import PromptLibrary
from app.observability.langfuse import set_metadata
from app.observability.otel import tracer


class NodeContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system_prompt: str
    needs: list[BlockName]


class ContextConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pipeline: list[str]
    window_messages: int
    reserved_loop_headroom_tokens: int
    nodes: dict[str, NodeContext]

    @model_validator(mode="after")
    def _known_steps(self) -> "ContextConfig":
        unknown = [name for name in self.pipeline if name not in STEPS]
        if unknown:
            raise ValueError(f"unknown context steps: {unknown}; known: {sorted(STEPS)}")
        return self


def load_context_config(config_dir: Path) -> ContextConfig:
    raw = yaml.safe_load((config_dir / "context.yaml").read_text(encoding="utf-8"))
    return ContextConfig.model_validate(raw)


class ContextAssembler:
    def __init__(self, config: ContextConfig, prompts: PromptLibrary, counter: TokenCounter):
        self.config = config
        deps = StepDeps(prompts=prompts, counter=counter, window_messages=config.window_messages)
        self.steps = [STEPS[name](deps) for name in config.pipeline]

    def node(self, name: str) -> NodeContext:
        return self.config.nodes[name]

    def budget(self, tier: str, spec: TierSpec, *, max_input: int | None = None) -> Budget:
        return Budget.for_tier(
            tier, spec, headroom=self.config.reserved_loop_headroom_tokens, max_input=max_input
        )

    async def assemble(self, request: AssemblyRequest, budget: Budget) -> ContextPackage | NeedsMore:
        with tracer.start_as_current_span("context.assemble") as span:
            package = ContextPackage(
                node=request.node,
                tier=budget.tier,
                locale=request.locale,
                budget_limit=budget.limit,
                request=request,
            )
            for step in self.steps:
                if not step.wanted(request):
                    continue
                result = await step.apply(package, budget)
                if isinstance(result, NeedsMore):
                    _record(span, result.package, deficit=result.deficit_tokens)
                    return result
                package = result
            _record(span, package)
            return package


def _record(span, package: ContextPackage, deficit: int | None = None) -> None:
    accounting = package.accounting()
    span.set_attribute("context.node", package.node)
    span.set_attribute("context.tier", package.tier)
    span.set_attribute("context.budget_limit", package.budget_limit)
    span.set_attribute("context.total_tokens", accounting["total_tokens"])
    for block in accounting["blocks"]:
        span.set_attribute(f"context.block.{block['name']}.tokens", block["tokens"])
    for drop in package.drops:
        span.add_event("context.drop", {"block": drop.block, "tokens": drop.tokens, "reason": drop.reason})
    if deficit is not None:
        span.set_attribute("context.needs_more.deficit_tokens", deficit)
    set_metadata(
        span,
        {
            "block_tokens": {b["name"]: b["tokens"] for b in accounting["blocks"]},
            "context": accounting,
        },
    )
