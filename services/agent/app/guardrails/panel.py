"""The admin test panel (README §10.4): one policy, stored or a draft, on or off, against
sample text, on the runtime engine path (the same checkers, the same combination
rules), with nothing recorded. The guardrails eval suite runs through it (I6)."""

from dataclasses import dataclass
from typing import Any

from app.guardrails.checkpoints import Checkpoint
from app.guardrails.engine import GuardEngine
from app.guardrails.schema import (
    TEXT_CHECKPOINTS,
    CheckResult,
    DataPayload,
    GuardContext,
    GuardOutcome,
    Policy,
)
from app.identity import Principal
from app.tools.spec import ToolOutput


class PanelError(ValueError):
    pass


@dataclass(frozen=True)
class PanelResult:
    checkpoint: Checkpoint
    target: str | None
    action: str
    outcome: GuardOutcome
    result: CheckResult | None
    output_text: str | None
    error: str | None


def _payload(checkpoint: Checkpoint, text: str, target: str | None) -> Any:
    match checkpoint:
        case Checkpoint.INPUT:
            return {"message": text, "attachments": []}
        case Checkpoint.OUTPUT:
            return {"answer": text, "citations": []}
        case Checkpoint.DATA:
            return DataPayload(text=text, target=target)
        case Checkpoint.PRE_TOOL:
            return {"tool": "panel_tool", "args": {"input": text}}
        case Checkpoint.POST_TOOL:
            return ToolOutput(content=text)
        case Checkpoint.TOOL_LIST:
            return [text]
        case Checkpoint.MODEL_TIER:
            return {"role": "reasoning", "step": None, "tier": text.strip(), "cap": "heavy"}
    raise PanelError(f"unknown checkpoint {checkpoint}")


def _output_text(checkpoint: Checkpoint, payload: Any) -> str | None:
    if checkpoint not in TEXT_CHECKPOINTS:
        return None
    if isinstance(payload, DataPayload):
        return payload.text
    if isinstance(payload, ToolOutput):
        return payload.content
    return payload.get("message") if checkpoint == Checkpoint.INPUT else payload.get("answer")


async def try_policy(
    engine: GuardEngine,
    policy: Policy,
    text: str,
    *,
    principal: Principal,
    checkpoint: str | None = None,
    target: str | None = None,
) -> PanelResult:
    point = Checkpoint(checkpoint) if checkpoint else policy.checkpoints[0]
    if point not in policy.checkpoints:
        raise PanelError(f"policy {policy.id} does not run at {point}")
    # A DATA payload always names its target (A4); no other checkpoint has one.
    default = (policy.config.get("targets") or ["answer"])[0]
    target = (target or default) if point == Checkpoint.DATA else None
    outcome = await engine.evaluate(
        point, _payload(point, text, target), principal=principal, context=GuardContext(only=policy)
    )
    result = outcome.results.get(policy.ref)
    error = None
    if result is None:
        failed = next((e for e in outcome.events if e.policy_id == policy.id), None)
        error = failed.detail if failed else "not evaluated"
    # TOOL_LIST filters rather than ends the turn: a removed capability is the block.
    action = (
        ("block" if not outcome.payload else "allow") if point == Checkpoint.TOOL_LIST else outcome.action
    )
    return PanelResult(
        checkpoint=point,
        target=target,
        action=action,
        outcome=outcome,
        result=result,
        output_text=_output_text(point, outcome.payload),
        error=error,
    )
