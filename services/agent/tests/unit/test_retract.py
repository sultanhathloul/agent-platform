"""Streaming before guards, interim (ADR-0039): when validate, OUTPUT or DATA fail
after tokens were streamed, the node emits `retract` before the turn ends in an
error. persist_state stores the post-guard text and keeps the streamed text for
audit only (ADR-0048)."""

from types import SimpleNamespace

import pytest
from langgraph.graph.message import add_messages

from app.errors import TurnError
from app.graph.nodes.guard import guard_data, guard_output
from app.graph.nodes.persist import persist_state
from app.graph.nodes.validate import validate
from app.guardrails.schema import GuardEventRecord, GuardOutcome
from app.identity import Principal
from app.text.script import RetryPolicy, ScriptPolicy, ValidationConfig

VALIDATION = ValidationConfig(
    scripts={"ar": ScriptPolicy(letters=["ARABIC", "LATIN"])},
    retry=RetryPolicy(issues=[], attempts=0, temperature=0.0),
)


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
    services = SimpleNamespace(guard=engine, validation=VALIDATION)
    context = SimpleNamespace(services=services, principal=Principal(user_id="u"))
    return SimpleNamespace(context=context, stream_writer=written.append), written


def _state(answer: str) -> dict:
    return {
        "user_message": "ما عاصمة المملكة؟",
        "locale": "ar",
        "turn_id": "turn-1",
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


async def test_redaction_persists_the_post_guard_text_and_keeps_the_shown_text_for_audit():
    shown = "رقم الجوال هو 0501234567"
    redacted = "رقم الجوال هو [REDACTED:phone]"
    runtime, written = _runtime(_Engine("redact", payload=redacted))
    state = _state(shown)
    update = await guard_data(state, runtime)
    assert [w["type"] for w in written] == ["guard"]

    persisted = await persist_state({**state, **update}, runtime)
    user, assistant = persisted["messages"]
    assert user == {"role": "user", "content": "ما عاصمة المملكة؟"}
    assert assistant == {"role": "assistant", "content": redacted, "turn_id": "turn-1", "shown_answer": shown}

    # In the checkpointed history the redacted text is the content; the shown text is audit metadata.
    _, reply = add_messages([], persisted["messages"])
    assert reply.content == redacted
    assert reply.additional_kwargs == {"turn_id": "turn-1", "shown_answer": shown}


async def test_unchanged_answer_is_persisted_once():
    runtime, _ = _runtime()
    persisted = await persist_state(_state("العاصمة هي الرياض."), runtime)
    assert persisted["messages"][1] == {
        "role": "assistant",
        "content": "العاصمة هي الرياض.",
        "turn_id": "turn-1",
    }
