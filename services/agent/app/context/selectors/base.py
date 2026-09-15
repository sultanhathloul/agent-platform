import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, Protocol

from app.context.budget import Budget, NeedsMore
from app.context.config import BlockBudget, ContextConfig
from app.context.package import PROTECTED_BLOCKS, AssemblyRequest, Block, BlockName, ContextPackage, Drop
from app.context.tokens import TokenCounter
from app.memory.types import MemoryHit, MemoryScope
from app.models.prompts import PromptLibrary

log = logging.getLogger(__name__)


class MemoryReader(Protocol):
    """Top-k memory items of one (tenant, user) by similarity to `query` (README §8).
    Returns nothing when that user's memory is disabled."""

    async def retrieve(self, scope: MemoryScope, query: str, top_k: int) -> list[MemoryHit]: ...


@dataclass(frozen=True)
class StepDeps:
    prompts: PromptLibrary
    counter: TokenCounter
    config: ContextConfig
    memory: MemoryReader | None = None


class Step(ABC):
    """One pipeline stage: `apply(package, budget) -> package`, or NeedsMore
    when the required blocks cannot fit."""

    name: ClassVar[str]
    # The block this step produces. The step runs only if the request needs that
    # block; None means it always runs. The user block is always produced, and the
    # system block whenever the request names a system prompt (ADR-0020).
    block: ClassVar[BlockName | None] = None

    def __init__(self, deps: StepDeps):
        self.deps = deps

    def wanted(self, request: AssemblyRequest) -> bool:
        if self.block == "system":
            return request.system_prompt is not None
        return self.block is None or self.block in PROTECTED_BLOCKS or self.block in request.needs

    def make_block(self, name: BlockName, role: str, content: str, **meta: object) -> Block:
        return Block(
            name=name, role=role, content=content, tokens=self.deps.counter.count(content), meta=meta
        )

    def budget_for(self, package: ContextPackage, block: BlockName | None = None) -> BlockBudget:
        return self.deps.config.block_budget(package.node, block or self.block)

    def drop(self, package: ContextPackage, block: BlockName, tokens: int, reason: str) -> None:
        """Every cut is recorded on the package (and so on the span) and logged (README §7 step 9)."""
        package.drops.append(Drop(block=block, tokens=tokens, reason=reason))
        log.info("context drop node=%s block=%s tokens=%d reason=%s", package.node, block, tokens, reason)

    @abstractmethod
    async def apply(self, package: ContextPackage, budget: Budget) -> ContextPackage | NeedsMore: ...
