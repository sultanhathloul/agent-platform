"""config/context.yaml (README §7, ADR-0056): pipeline order, window size, per-block
budgets, drop order, the compress pass, the rolling-summary trigger and the core
nodes that call a model."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.context.package import BlockName

DroppableBlock = Literal["window", "retrieval", "memory", "summary"]
FormBlock = Literal["retrieval", "tool_results", "findings"]
Form = Literal["full", "summary", "reference"]

# Blocks a step produces within a budget of its own; system and user_message are never cut.
BUDGETED_BLOCKS: tuple[BlockName, ...] = (
    "skill",
    "state_digest",
    "summary",
    "window",
    "memory",
    "retrieval",
    "tool_results",
    "findings",
)
# Nodes core code calls a model for; each needs an entry.
CORE_NODES = ("answer", "summarizer", "memory_writer")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BlockBudget(_Strict):
    max_tokens: int = Field(gt=0)
    top_k: int | None = Field(default=None, gt=0)


class CompressPass(_Strict):
    window_messages: int = Field(ge=0)
    memory_top_k: int = Field(ge=0)
    retrieval_top_k: int = Field(ge=0)
    summary_max_tokens: int = Field(gt=0)
    tool_results_max_tokens: int = Field(gt=0)


class SummaryTrigger(_Strict):
    trigger_tokens: int = Field(gt=0)


class NodeContext(_Strict):
    system_prompt: str
    request_prompt: str | None = None
    needs: list[BlockName]
    forms: dict[FormBlock, Form] = {}
    blocks: dict[BlockName, BlockBudget] = {}


class ContextConfig(_Strict):
    pipeline: list[str]
    window_messages: int = Field(ge=0)
    reserved_loop_headroom_tokens: int = Field(ge=0)
    blocks: dict[BlockName, BlockBudget]
    drop_order: list[DroppableBlock]
    compress: CompressPass
    summary: SummaryTrigger
    nodes: dict[str, NodeContext]

    @model_validator(mode="after")
    def _complete(self) -> "ContextConfig":
        missing = [block for block in BUDGETED_BLOCKS if block not in self.blocks]
        if missing:
            raise ValueError(f"blocks: no budget for {missing}")
        for block in ("memory", "retrieval"):
            if self.blocks[block].top_k is None:
                raise ValueError(f"blocks.{block}.top_k is required")
        if len(set(self.drop_order)) != len(self.drop_order):
            raise ValueError("drop_order names a block twice")
        absent = [node for node in CORE_NODES if node not in self.nodes]
        if absent:
            raise ValueError(f"nodes: missing {absent}")
        return self

    def block_budget(self, node: str, block: BlockName) -> BlockBudget:
        """The node's own budget for `block` when it declares one, else the global one."""
        own = self.nodes.get(node)
        if own is not None and block in own.blocks:
            return own.blocks[block]
        return self.blocks[block]

    def form(self, node: str, block: FormBlock) -> Form:
        own = self.nodes.get(node)
        return own.forms.get(block, "full") if own is not None else "full"


def load_context_config(config_dir: Path) -> ContextConfig:
    raw = yaml.safe_load((config_dir / "context.yaml").read_text(encoding="utf-8"))
    return ContextConfig.model_validate(raw)
