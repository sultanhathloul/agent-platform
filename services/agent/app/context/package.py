"""ContextPackage: the ordered blocks a model call sees, with token accounting (README §7)."""

from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

BlockName = Literal[
    "system",
    "state_digest",
    "window",
    "summary",
    "memory",
    "retrieval",
    "tool_results",
    "findings",
    "user_message",
]
BLOCK_NAMES: tuple[str, ...] = get_args(BlockName)

# Budget guard never drops these (README §7 step 9).
PROTECTED_BLOCKS: frozenset[str] = frozenset({"system", "user_message"})


class Block(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: BlockName
    role: Literal["system", "user", "assistant"]
    content: str
    tokens: int = 0
    meta: dict[str, Any] = {}


class Drop(BaseModel):
    block: BlockName
    tokens: int
    reason: str


class AssemblyRequest(BaseModel):
    """What a node asks Context Assembly for. `state` is a read-only AgentState snapshot."""

    model_config = ConfigDict(extra="forbid")

    node: str
    needs: list[BlockName]
    system_prompt: str | None = Field(
        default=None, description="Prompt id of the system block; None when the node needs no system block."
    )
    locale: str
    state: dict[str, Any]
    tools: list[str] = Field(default=[], description="Descriptions of permitted tools this node may use.")
    findings: list[dict[str, str]] = Field(default=[], description="[{capability, findings}] from this turn.")
    policy_hints: list[str] = []


class ContextPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node: str
    tier: str
    locale: str
    budget_limit: int
    blocks: list[Block] = []
    drops: list[Drop] = []
    prompt_refs: list[str] = []
    request: AssemblyRequest | None = Field(default=None, exclude=True)

    def total_tokens(self) -> int:
        return sum(block.tokens for block in self.blocks)

    def tokens_by_block(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for block in self.blocks:
            totals[block.name] = totals.get(block.name, 0) + block.tokens
        return totals

    def accounting(self) -> dict[str, Any]:
        """The record logged on the `context.assemble` span."""
        counts: dict[str, int] = {}
        for block in self.blocks:
            counts[block.name] = counts.get(block.name, 0) + 1
        return {
            "node": self.node,
            "tier": self.tier,
            "budget_limit": self.budget_limit,
            "total_tokens": self.total_tokens(),
            "blocks": [
                {"name": name, "tokens": tokens, "count": counts[name]}
                for name, tokens in self.tokens_by_block().items()
            ],
            "drops": [drop.model_dump() for drop in self.drops],
            "prompts": self.prompt_refs,
        }

    def to_messages(self) -> list[dict[str, str]]:
        """System blocks merge into one leading system message; conversation
        blocks follow in pipeline order, ending with the user message."""
        system = "\n\n".join(b.content for b in self.blocks if b.role == "system" and b.content)
        messages = [{"role": "system", "content": system}] if system else []
        messages += [{"role": b.role, "content": b.content} for b in self.blocks if b.role != "system"]
        return messages
