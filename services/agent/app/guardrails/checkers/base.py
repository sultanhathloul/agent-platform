"""The checker contract (README §10.2): `check(payload, config) -> CheckResult`.

A checker answers one question, whether its policy's config matches the text, and
says where. It never decides an action, never reads another policy and holds no
capability-specific logic (README §18.4). A checker that cannot answer raises
CheckerError; the engine then applies the policy's `on_error` (D8)."""

from dataclasses import dataclass
from typing import Any, Protocol

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


class Checker(Protocol):
    name: str

    async def check(self, payload: Any, config: Any, ctx: CheckContext) -> CheckResult: ...
