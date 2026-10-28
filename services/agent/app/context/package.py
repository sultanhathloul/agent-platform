"""ContextPackage: the ordered blocks a model call sees, with token accounting (README §7)."""

from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

from app.memory.types import MemoryScope

BlockName = Literal[
    "system",
    "skill",
    "report_kit",
    "state_digest",
    "window",
    "summary",
    "memory",
    "retrieval",
    "tool_results",
    "findings",
    "report_current",
    "user_message",
]
BLOCK_NAMES: tuple[str, ...] = get_args(BlockName)

# Budget guard never drops these (README §7 step 9).
PROTECTED_BLOCKS: frozenset[str] = frozenset({"system", "user_message"})
# The selected skill's instructions (phase 06, ADR-0300): a system-role block only the answer node
# requests, never dropped (it is not droppable in config/context.yaml), its size checked at startup.
SKILL_BLOCK = "skill"


# The blocks that form the current user message; only these merge (ADR-0076). The evidence
# blocks are data in the user role and travel with the question (owner decision S2).
CURRENT_MESSAGE_BLOCKS: frozenset[str] = frozenset(
    {"summary", "memory", "retrieval", "tool_results", "findings", "report_current", "user_message"}
)
# A report turn's blocks (reports PR, ADR-0380): the design kit's prompt part (system role, reviewed
# text) and, on a follow-up, the latest version being revised (user role, data). Only the report writer
# requests them; neither is ever dropped.
REPORT_KIT_BLOCK = "report_kit"
REPORT_CURRENT_BLOCK = "report_current"


class HistoryEndsWithUserMessage(RuntimeError):
    """The window ends with a user message where the current message begins. Pairs
    are skipped together (ADR-0076), so this is a core defect, never merged away."""


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


class SkillBlockRequest(BaseModel):
    """Which skill the `skill` block renders: its name and version (the `id` and `version` of
    `<skill>`) and its instructions, SKILL.md's body with the Markdown files it names (ADR-0318).
    The text is never written to state, a trace or the audit (G8)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    slug: str
    version: str
    instructions: str = Field(repr=False)


class ReportBlockRequest(BaseModel):
    """What the report writer's two blocks render: the design kit's text (design-system.md) and, on a
    follow-up, the version it revises, without the platform's marks (ADR-0380)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kit_id: str
    kit_version: str
    kit_text: str = Field(repr=False)
    current_title: str | None = None
    current_version: int | None = None
    current_html: str | None = Field(default=None, repr=False)


class AssemblyRequest(BaseModel):
    """What a node asks Context Assembly for. `state` is a read-only AgentState snapshot."""

    model_config = ConfigDict(extra="forbid")

    node: str
    needs: list[BlockName]
    system_prompt: str | None = Field(
        default=None, description="Prompt id of the system block; None when the node needs no system block."
    )
    user_prompt: str | None = Field(
        default=None,
        description="Prompt id rendered as the user block in place of state.user_message (helper nodes).",
    )
    locale: str
    state: dict[str, Any]
    tools: list[str] = Field(default=[], description="Descriptions of permitted tools this node may use.")
    findings: list[dict[str, str]] = Field(default=[], description="[{capability, findings}] from this turn.")
    retrieved: list[dict[str, Any]] = Field(
        default=[],
        description="Chunks {id, document_id?, source, page?, locator?, kb?, publisher?, published_on?, "
        "superseded_by?, score, text} for the answer, fetched at prepare time and checked at POST_TOOL (S4).",
    )
    tool_results: list[dict[str, Any]] = Field(
        default=[], description="This turn's tool results {tool, content, sources?}, already sanitized."
    )
    policy_hints: list[str] = []
    skill: SkillBlockRequest | None = Field(
        default=None, description="The turn's selected skill; its instructions are the `skill` block (ADR-0300)."
    )
    report: ReportBlockRequest | None = Field(
        default=None, description="A report turn's kit and current version (reports PR, ADR-0380)."
    )
    prompt_vars: dict[str, Any] = Field(
        default={}, description="Extra variables for the node's system and request prompts (guard judge)."
    )
    memory_scope: MemoryScope | None = Field(
        default=None, description="Whose memory the memory step reads; set by core code from the principal."
    )
    content_free: bool = Field(
        default=False,
        description="The call carries memory, summary or extractor text: no messages or output in its "
        "trace and no recorded package (D5).",
    )
    window_messages: int | None = Field(
        default=None, ge=0, description="Overrides the configured window size for this request."
    )
    compress: bool = Field(
        default=False, description="The harder pass after NeedsMore resolved to compress (README §19.3)."
    )


class ContextPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node: str
    tier: str
    locale: str
    budget_limit: int
    compressed: bool = False
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
            "compressed": self.compressed,
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
        blocks follow in pipeline order, ending with the user message. Merging is
        confined to the current-message group (ADR-0076): adjacent summary, memory
        and user_message blocks of one role join into one message, so the
        delimited data blocks (ADR-0068) travel with the question. A history
        (window) message is never merged with a neighbour; history that ends with
        a user message right before the group is refused, never merged."""
        system = "\n\n".join(b.content for b in self.blocks if b.role == "system" and b.content)
        messages = [{"role": "system", "content": system}] if system else []
        previous: Block | None = None
        for block in self.blocks:
            if block.role == "system":
                continue
            if previous is not None and previous.role == block.role:
                if previous.name in CURRENT_MESSAGE_BLOCKS and block.name in CURRENT_MESSAGE_BLOCKS:
                    messages[-1]["content"] += "\n\n" + block.content
                    previous = block
                    continue
                if previous.name == "window" and block.name in CURRENT_MESSAGE_BLOCKS:
                    raise HistoryEndsWithUserMessage(
                        f"node {self.node}: the window ends with a user message before {block.name}"
                    )
            messages.append({"role": block.role, "content": block.content})
            previous = block
        return messages
