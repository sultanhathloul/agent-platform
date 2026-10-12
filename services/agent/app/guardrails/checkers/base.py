"""The checker contract (README §10.2): `check(payload, config) -> CheckResult`.

A checker answers one question, whether its policy's config matches the text, and
says where. It never decides an action, never reads another policy and holds no
capability-specific logic (README §18.4). A checker that cannot answer raises
CheckerError; the engine then applies the policy's `on_error` (D8)."""

from dataclasses import dataclass
from typing import Any, Protocol

from app.guardrails.checkpoints import Checkpoint
from app.guardrails.schema import CheckResult, Policy
from app.identity import Principal


class CheckerError(RuntimeError):
    """The checker could not evaluate (service down, malformed model answer, ...)."""


@dataclass(frozen=True)
class CheckContext:
    principal: Principal
    policy: Policy
    locale: str = "ar"
    turn: Any = None  # the running TurnContext, when there is one
    target: str | None = None  # the DATA target, when the checkpoint is DATA
    checkpoint: Checkpoint | None = None  # where the text is checked
    panel: bool = False  # the admin test panel: nothing is recorded or cached
    draft: bool = False  # the policy is an unsaved draft: its id@version names nothing stored


class Checker(Protocol):
    name: str

    async def check(self, payload: Any, config: Any, ctx: CheckContext) -> CheckResult: ...
