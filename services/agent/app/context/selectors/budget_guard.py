import logging

from app.context.budget import Budget, NeedsMore
from app.context.package import ContextPackage, Drop
from app.context.selectors.base import Step

log = logging.getLogger(__name__)


class BudgetGuard(Step):
    """Hard cap on context size (README §7 step 9).

    Drop order: old window messages → lowest-scored chunks → memory → summary
    shortening. Phase 01 has only window messages to drop. System instructions
    and the user message are never dropped. If the package still does not fit,
    returns NeedsMore and the tier resolver decides (phase 02).
    """

    name = "budget_guard"

    async def apply(self, package: ContextPackage, budget: Budget) -> ContextPackage | NeedsMore:
        over = package.total_tokens() - budget.limit
        for block in [b for b in package.blocks if b.name == "window"]:  # oldest first
            if over <= 0:
                break
            package.blocks.remove(block)
            package.drops.append(Drop(block="window", tokens=block.tokens, reason="over_budget"))
            log.info("context drop node=%s block=window tokens=%d", package.node, block.tokens)
            over -= block.tokens
        if over > 0:
            return NeedsMore(deficit_tokens=over, package=package)
        return package
