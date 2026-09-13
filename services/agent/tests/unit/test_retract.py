"""Streaming before guards, interim (ADR-0039): when validate, OUTPUT or DATA fail
after tokens were streamed, the node emits `retract` before the turn ends in an
error, and persist_state stores what the user saw."""

from types import SimpleNamespace

import pytest

from app.errors import TurnError
from app.graph.nodes.guard import guard_data, guard_output
from app.graph.nodes.persist import persist_state
from app.graph.nodes.validate import validate
from app.guardrails.schema import GuardEventRecord, GuardOutcome
from app.identity import Principal


class _Engine:
    def __init__(self, action: str, payload=None):
        self.action, self.payload = action, payload

    async def evaluate(self, checkpoint, payload, *, principal=None, scope=None):
        return GuardOutcome(
            action=self.action,
            payload=payload if self.payload is None else self.payload,
            events=[GuardEventRecord(checkpoint=checkpoint, policy_id="test-policy", action=self.action)],
        )


def _runtime(engine=None):
    written: list[dict] = []
    context = SimpleNamespace(services=SimpleNamespace(guard=engine), principal=Principal(user_id="u"))
    return SimpleNamespace(context=context, stream_writer=written.append), written


def _state(answer: str) -> dict:
    return {
        "user_message": "ما عاصمة المملكة؟",
        "locale": "ar",
        "draft_answer": answer,
        "shown_answer": answer,
        "capability_outputs": [],
        "citations": [],
        "guard_events": [],
    }


async def test_validation_failure_after_streaming_retracts_then_fails():
    runtime, written = _runtime()
    with pytest.raises(TurnError) as info:
        await validate(_state("العاصمة هي الرياض 北京"), runtime)
    assert info.value.code == "validation_failed"
    assert written == [{"type": "retract", "data": {"reason": "validation_failed"}}]


async def test_nothing_streamed_means_nothing_to_retract():
    runtime, written = _runtime()
    with pytest.raises(TurnError):
        await validate(_state(""), runtime)
    assert written == []


@pytest.mark.parametrize(("node", "code"), [(guard_output, "output_blocked"), (guard_data, "data_blocked")])
async def test_blocking_guard_after_streaming_retracts_then_fails(node, code):
    runtime, written = _runtime(_Engine("block"))
    with pytest.raises(TurnError) as info:
        await node(_state("العاصمة هي الرياض."), runtime)
    assert info.value.code == code
    assert [w["type"] for w in written] == ["guard", "retract"]
    assert written[1]["data"] == {"reason": code}


async def test_redaction_keeps_the_stream_and_persists_both_texts():
    shown = "رقم الجوال هو 0501234567"
    redacted = "رقم الجوال هو [REDACTED:phone]"
    runtime, written = _runtime(_Engine("redact", payload=redacted))
    state = _state(shown)
    update = await guard_data(state, runtime)
    assert [w["type"] for w in written] == ["guard"]

    persisted = await persist_state({**state, **update}, runtime)
    user, assistant = persisted["messages"]
    assert user == {"role": "user", "content": "ما عاصمة المملكة؟"}
    assert assistant == {"role": "assistant", "content": shown, "post_guard_draft": redacted}


async def test_unchanged_answer_is_persisted_once():
    runtime, _ = _runtime()
    persisted = await persist_state(_state("العاصمة هي الرياض."), runtime)
    assert persisted["messages"][1] == {"role": "assistant", "content": "العاصمة هي الرياض."}
