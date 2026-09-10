from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

from app.context.budget import Budget, NeedsMore
from app.context.package import PROTECTED_BLOCKS, AssemblyRequest, Block, BlockName, ContextPackage
from app.context.tokens import TokenCounter
from app.models.prompts import PromptLibrary


@dataclass(frozen=True)
class StepDeps:
    prompts: PromptLibrary
    counter: TokenCounter
    window_messages: int


class Step(ABC):
    """One pipeline stage: `apply(package, budget) -> package`, or NeedsMore
    when the required blocks cannot fit."""

    name: ClassVar[str]
    # The block this step produces. The step runs only if the request needs that
    # block; None means it always runs. System and user blocks are always produced.
    block: ClassVar[BlockName | None] = None

    def __init__(self, deps: StepDeps):
        self.deps = deps

    def wanted(self, request: AssemblyRequest) -> bool:
        return self.block is None or self.block in PROTECTED_BLOCKS or self.block in request.needs

    def make_block(self, name: BlockName, role: str, content: str, **meta: object) -> Block:
        return Block(
            name=name, role=role, content=content, tokens=self.deps.counter.count(content), meta=meta
        )

    @abstractmethod
    async def apply(self, package: ContextPackage, budget: Budget) -> ContextPackage | NeedsMore: ...
