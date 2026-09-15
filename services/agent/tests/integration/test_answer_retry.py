"""The answer's script retry (ADR-0060, ADR-0061): an Arabic answer that fails the
script check is withdrawn with `retract` and retried once at the configured
temperature on the same context package; the retry is kept when it passes, and
the turn ends as validation_failed when it fails too.

Every model call still goes to LiteLLM through the real gateway. The mock model
never drifts, so for the answer the test replaces the text of chosen attempts
with a drifting one and streams it word by word, as a drifting model would."""

import dataclasses
import re
import uuid

import pytest

from app.api.runner import TurnRunner
from app.api.schemas import TurnRequest
from app.identity import Principal

pytestmark = pytest.mark.integration

MOCK_ANSWER = "هذه إجابة تجريبية من النموذج الوهمي. This is a mock answer."
RIYAL = "العملة الرسمية في المملكة العربية السعودية هي الريyal السعودي."
LISBOA = "عاصمة البرتغال هي لисboa."


class _DriftingGateway:
    def __init__(self, real, drafts):
        self._real = real
        self._drafts = list(drafts)
        self.temperatures: list[float | None] = []

    async def call(self, decision, context, *, on_token=None, temperature=None):
        if decision.step != "answer":
            return await self._real.call(decision, context, on_token=on_token, temperature=temperature)
        self.temperatures.append(temperature)
        draft = self._drafts.pop(0)
        if draft is None:
            return await self._real.call(decision, context, on_token=on_token, temperature=temperature)
        result = await self._real.call(decision, context, temperature=temperature)
        for piece in re.findall(r"\S+\s*", draft):
            if on_token:
                on_token(piece)
        return result.model_copy(update={"text": draft})


async def _turn(services, drafts, *, locale="ar"):
    gateway = _DriftingGateway(services.gateway, drafts)
    request = TurnRequest(
        conversation_id=str(uuid.uuid4()),
        principal=Principal(user_id=f"u-{uuid.uuid4().hex[:8]}", permissions=("cap:general",)),
        message="ما العملة الرسمية في المملكة؟",
        locale=locale,
    )
    events = [
        e async for e in TurnRunner(dataclasses.replace(services, gateway=gateway)).stream(request, "req")
    ]
    snapshot = await services.graph.aget_state({"configurable": {"thread_id": request.conversation_id}})
    return events, snapshot, gateway


def _answer_node(spans):
    return next(s for s in spans.get_finished_spans() if s.name == "node.answer")


def _text(events) -> str:
    return "".join(e.data.text for e in events if e.type == "token")


async def test_a_failed_script_check_is_retried_once_and_the_passing_retry_is_kept(services, spans, litellm):
    events, snapshot, gateway = await _turn(services, [RIYAL, None])

    types = [e.type for e in events]
    retract = types.index("retract")
    assert types.count("retract") == 1 and types[-1] == "done"
    assert events[retract].data.reason == "answer_retry"
    assert _text(events[:retract]) == RIYAL and _text(events[retract:]) == MOCK_ANSWER
    assert events[-1].data.answer == MOCK_ANSWER
    assert snapshot.values["messages"][-1].content == MOCK_ANSWER

    assert gateway.temperatures == [None, 0.0]
    answer_calls = [
        s
        for s in spans.get_finished_spans()
        if s.name == "model.call" and s.attributes.get("model.step") == "answer"
    ]
    assert [s.attributes["model.temperature"] for s in answer_calls] == [0.2, 0.0]
    node = _answer_node(spans)
    assert node.attributes["answer.retry"] is True
    assert node.attributes["answer.retry.issues"] == "mixed_script_token"
    assert node.attributes["answer.retry.passed"] is True


async def test_a_retry_that_fails_too_ends_the_turn_as_validation_failed(services, spans, litellm):
    events, snapshot, gateway = await _turn(services, [RIYAL, LISBOA])

    assert [e.data.reason for e in events if e.type == "retract"] == ["answer_retry", "validation_failed"]
    assert events[-1].type == "error" and events[-1].data.code == "validation_failed"
    assert "done" not in [e.type for e in events]
    assert gateway.temperatures == [None, 0.0]
    node = _answer_node(spans)
    assert node.attributes["answer.retry"] is True and node.attributes["answer.retry.passed"] is False
    assert not snapshot.values.get("messages")  # an errored turn persists nothing


async def test_with_the_retry_off_a_script_failure_ends_the_turn(services, spans, litellm):
    retry = services.validation.retry.model_copy(update={"attempts": 0})
    no_retry = dataclasses.replace(
        services, validation=services.validation.model_copy(update={"retry": retry})
    )
    events, _, gateway = await _turn(no_retry, [RIYAL])
    assert gateway.temperatures == [None]
    assert [e.data.reason for e in events if e.type == "retract"] == ["validation_failed"]
    assert events[-1].data.code == "validation_failed"
    assert _answer_node(spans).attributes["answer.retry"] is False


async def test_a_locale_without_a_script_policy_is_not_retried(services, litellm):
    events, _, gateway = await _turn(services, ["The capital of Russia is Москва."], locale="en")
    assert gateway.temperatures == [None]
    assert [e.type for e in events if e.type in {"retract", "error", "done"}] == ["done"]
