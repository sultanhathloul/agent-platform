"""What leaves the agent after the guards (ADR-0039 full fix, D3). Redaction happens
before text is released, so the persisted reply and the shown text are the same
redacted text; `retract` remains only for `validate` on the complete answer and for
a blocking policy that a mid-turn reload brings in after the text streamed.
persist_state stores the post-guard text (ADR-0048)."""

from types import SimpleNamespace

import pytest
from langgraph.graph.message import add_messages

from app.errors import TurnError
from app.graph.deps import TurnContext
from app.graph.nodes.guard import guard_data, guard_output, release_answer
from app.graph.nodes.persist import persist_state
from app.graph.nodes.validate import validate
from app.guardrails.config import StreamingConfig
from app.guardrails.schema import DataPayload, GuardEventRecord, GuardOutcome
from app.identity import Principal
from app.text.script import RetryPolicy, ScriptPolicy, ValidationConfig

VALIDATION = ValidationConfig(
    scripts={"ar": ScriptPolicy(letters=["ARABIC", "LATIN"])},
    retry=RetryPolicy(issues=[], attempts=0, temperature=0.0),
)
# Page attribution's rule (ADR-0140); these tests carry no sources, so nothing is cited.
STREAMING = SimpleNamespace(streaming=StreamingConfig(terminators=[".", "؟"], flush_chars=240))


class _Engine:
    """Answers every checkpoint with one policy's verdict."""

    def __init__(self, action: str, text: str | None = None):
        self.action, self.text = action, text

    async def evaluate(self, checkpoint, payload, *, principal=None, context=None):
        if self.text is not None:
            payload = (
                payload.model_copy(update={"text": self.text})
                if isinstance(payload, DataPayload)
                else {**payload, "answer": self.text}
            )
        event = GuardEventRecord(
            checkpoint=checkpoint, policy_id="test-policy", policy_version=1, action=self.action
        )
        return GuardOutcome(
            action=self.action,
            payload=payload,
            events=[event],
            decided_by="test-policy@1",
            message_ar="رسالة السياسة" if self.action == "block" else None,
            message_en="The policy's message" if self.action == "block" else None,
        )


def _runtime(engine=None):
    written: list[dict] = []
    services = SimpleNamespace(guard=engine, validation=VALIDATION, guard_config=STREAMING)
    context = TurnContext(
        services=services, principal=Principal(user_id="u"), request_id="r", turn_id="turn-1"
    )
    context.writer = written.append
    return SimpleNamespace(context=context, stream_writer=written.append), written


def _state(answer: str, *, mode: str = "streaming") -> dict:
    return {
        "user_message": "ما عاصمة المملكة؟",
        "locale": "ar",
        "turn_id": "turn-1",
        "answer_mode": mode,
        "draft_answer": answer,
        "shown_answer": answer if mode == "streaming" else None,
        "capability_outputs": [],
        "citations": [],
        "guard_events": [],
        "intent": {"capabilities": ["general"]},
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


async def test_a_block_that_arrives_after_streaming_retracts_then_fails_with_the_policy_message():
    runtime, written = _runtime(_Engine("block"))
    with pytest.raises(TurnError) as info:
        await guard_output(_state("العاصمة هي الرياض."), runtime)
    assert (info.value.code, info.value.message_ar, info.value.policy_id) == (
        "output_blocked",
        "رسالة السياسة",
        "test-policy@1",
    )
    assert [w["type"] for w in written] == ["guard", "retract"]
    assert written[1]["data"] == {"reason": "output_blocked"}


@pytest.mark.parametrize(("node", "code"), [(guard_output, "output_blocked"), (guard_data, "data_blocked")])
async def test_a_buffered_block_ends_the_turn_before_any_text_left(node, code):
    runtime, written = _runtime(_Engine("block"))
    with pytest.raises(TurnError) as info:
        await node(_state("العاصمة هي الرياض.", mode="buffered"), runtime)
    assert info.value.code == code and info.value.message_en == "The policy's message"
    assert [w["type"] for w in written] == ["guard"]  # no token, no retract


async def test_streaming_a_clean_final_pass_changes_and_reports_nothing():
    runtime, written = _runtime(_Engine("allow"))
    assert await guard_data(_state("العاصمة هي الرياض."), runtime) == {}
    assert written == []


async def test_streaming_a_final_pass_redaction_retracts_and_resends_the_redacted_text():
    """ADR-0111: a span no sentence held whole is caught on the whole released text."""
    redacted = "رقمي [REDACTED:SA_MOBILE] للتجربة."
    runtime, written = _runtime(_Engine("redact", text=redacted))
    state = _state("رقمي 050123\n4567 للتجربة.")
    state["shown_answer"] = state["draft_answer"]
    update = await guard_data(state, runtime)
    assert update["draft_answer"] == update["shown_answer"] == redacted
    types = [w["type"] for w in written]
    assert types[0] == "guard" and types[1] == "retract" and set(types[2:]) == {"token"}
    assert written[1]["data"]["reason"] == "answer_redacted"
    assert "".join(w["data"]["text"] for w in written[2:]) == redacted


async def test_streaming_a_final_pass_block_retracts_and_ends_the_turn():
    runtime, written = _runtime(_Engine("block"))
    state = _state("العاصمة هي الرياض.")
    state["shown_answer"] = state["draft_answer"]
    with pytest.raises(TurnError) as info:
        await guard_data(state, runtime)
    assert info.value.code == "data_blocked"
    assert [w["type"] for w in written] == ["guard", "retract"]


async def test_a_buffered_redaction_releases_only_the_redacted_text_and_persists_it():
    shown = "رقم الجوال هو 0501234567. شكرًا"
    redacted = "رقم الجوال هو [REDACTED:SA_MOBILE]. شكرًا"
    runtime, written = _runtime(_Engine("redact", text=redacted))
    state = _state(shown, mode="buffered")
    state |= await guard_data(state, runtime)
    state |= await release_answer(state, runtime)
    tokens = [w["data"]["text"] for w in written if w["type"] == "token"]
    assert "".join(tokens) == redacted and len(tokens) == 2  # a sentence per token event
    assert not any("0501234567" in t for t in tokens)
    assert [w["type"] for w in written][0] == "guard"

    persisted = await persist_state(state, runtime)
    user, assistant = persisted["messages"]
    assert user == {"role": "user", "content": "ما عاصمة المملكة؟"}
    # What was shown and what is stored are the same redacted text: no audit copy of the raw text.
    assert assistant == {"role": "assistant", "content": redacted, "turn_id": "turn-1"}
    _, reply = add_messages([], persisted["messages"])
    assert reply.content == redacted and reply.additional_kwargs == {"turn_id": "turn-1"}


async def test_unchanged_answer_is_persisted_once():
    runtime, _ = _runtime()
    persisted = await persist_state(_state("العاصمة هي الرياض."), runtime)
    assert persisted["messages"][1] == {
        "role": "assistant",
        "content": "العاصمة هي الرياض.",
        "turn_id": "turn-1",
    }
