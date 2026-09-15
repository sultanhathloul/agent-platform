from app.context.budget import Budget, NeedsMore
from app.context.package import ContextPackage
from app.context.selectors.base import Step
from app.context.selectors.blocks import render_summary


class BudgetGuard(Step):
    """Hard cap on context size (README §7 step 9).

    Drops in `drop_order` from config/context.yaml: old window messages, then the
    lowest-scored chunks, then memory, then shortens the summary. System
    instructions and the user message are never dropped. Every drop is recorded.
    If the package still does not fit, returns NeedsMore and the caller resolves it
    as compress or escalate, once (README §19.3, ADR-0056).
    """

    name = "budget_guard"

    async def apply(self, package: ContextPackage, budget: Budget) -> ContextPackage | NeedsMore:
        over = package.total_tokens() - budget.limit
        for name in self.deps.config.drop_order:
            if over <= 0:
                break
            over = (
                self._shorten_summary(package, over) if name == "summary" else self._drop(package, name, over)
            )
        if over > 0:
            return NeedsMore(deficit_tokens=over, package=package)
        return package

    def _drop(self, package: ContextPackage, name: str, over: int) -> int:
        candidates = [b for b in package.blocks if b.name == name]  # window: oldest first
        if name == "retrieval":
            candidates.sort(key=lambda b: b.meta.get("score") or 0.0)  # lowest-scored first
        for block in candidates:
            if over <= 0:
                break
            package.blocks.remove(block)
            self.drop(package, name, block.tokens, "over_budget")
            over -= block.tokens
        return over

    def _shorten_summary(self, package: ContextPackage, over: int) -> int:
        block = next((b for b in package.blocks if b.name == "summary"), None)
        if block is None:
            return over
        index = package.blocks.index(block)
        shorter = render_summary(
            self, package, block.meta.get("summary") or "", block.tokens - over, reason="over_budget"
        )
        if shorter is None:
            package.blocks.pop(index)
            self.drop(package, "summary", block.tokens, "over_budget")
            return over - block.tokens
        package.blocks[index] = shorter
        return over - (block.tokens - shorter.tokens)
