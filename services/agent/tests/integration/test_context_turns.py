"""Context Assembly through the API (README §7): a request that cannot fit ends as
context_overflow; the rolling summary kicks in past its trigger, skips retracted
messages and keeps folding once it exists; the debug endpoint returns a turn's
packages to the conversation's owner only (ADR-0056, ADR-0057, ADR-0059)."""

import json
import uuid

import pytest

from app.context.compress import truncate
from tests.fixtures.http import TOKEN, identity, post_turn

pytestmark = pytest.mark.integration

# A long Arabic paragraph (a turn takes at most 8,000 characters); tests cut it to a token count.
LONG = ("تقع المملكة العربية السعودية في جنوب غرب آسيا، وتبلغ مساحتها نحو مليوني كيلومتر مربع. " * 120)[:8000]


def _principal(tenant_id: str = "default") -> dict:
    return {"user_id": f"u-{uuid.uuid4().hex[:8]}", "tenant_id": tenant_id, "permissions": ["cap:general"]}


def _thread(conversation_id: str) -> dict:
    return {"configurable": {"thread_id": conversation_id}}


def _contexts(spans, node: str) -> list[dict]:
    return [
        json.loads(s.attributes["langfuse.observation.metadata.context"])
        for s in spans.get_finished_spans()
        if s.name == "context.assemble" and s.attributes.get("context.node") == node
    ]


def _calls(spans, role: str) -> list:
    return [
        s for s in spans.get_finished_spans() if s.name == "model.call" and s.attributes["model.role"] == role
    ]


def _count(context: dict, block: str) -> int:
    return next((b["count"] for b in context["blocks"] if b["name"] == block), 0)


async def test_context_overflow_ends_the_turn_through_the_api(client, services, litellm):
    conversation = str(uuid.uuid4())
    # 8,000 hieroglyphs are about 32,000 tokens: the user message fits no pass of any budget.
    events = await post_turn(
        client, conversation_id=conversation, principal=_principal(), message=chr(0x13000) * 8000
    )
    assert [(e["type"], e["data"].get("code")) for e in events] == [("error", "context_overflow")]
    assert not (await services.graph.aget_state(_thread(conversation))).values.get("messages")


async def test_the_summarizer_does_not_run_below_the_trigger(client, services, spans, litellm):
    conversation, principal = str(uuid.uuid4()), _principal()
    for message in ("ما عاصمة المملكة؟", "وما أكبر مدنها؟", "وما عملتها؟", "ومتى تأسست؟"):
        events = await post_turn(client, conversation_id=conversation, principal=principal, message=message)
        assert events[-1]["type"] == "done"
    assert _calls(spans, "summarizer") == []
    values = (await services.graph.aget_state(_thread(conversation))).values
    assert values.get("summary") is None and not values.get("summary_cursor")
    assert all(_count(context, "summary") == 0 for context in _contexts(spans, "answer"))


def _model_call(spans, role: str):
    [call] = _calls(spans, role)
    return call


async def test_the_summarizer_folds_older_messages_past_the_trigger(client, services, spans, litellm):
    counter, trigger = services.assembler.counter, services.assembler.config.summary.trigger_tokens
    message = truncate(LONG, 1750, counter)
    tokens = counter.count(message)
    # Four visible exchanges stay under the trigger; a fifth takes the history past it.
    assert 4 * tokens + 4 * 100 <= trigger < 5 * tokens

    conversation, principal = str(uuid.uuid4()), _principal()
    first = await post_turn(client, conversation_id=conversation, principal=principal, message=message)
    withdrawn = await client.post(
        f"/v1/conversations/{conversation}/turns/{first[-1]['turn_id']}/retract",
        json={"principal": principal, "reason": "audit_unavailable"},
        headers={"X-Internal-Token": TOKEN},
    )
    # The first exchange, question and reply, must not reach the summary (ADR-0048, ADR-0076).
    assert withdrawn.status_code == 204
    for _ in range(4):
        events = await post_turn(client, conversation_id=conversation, principal=principal, message=message)
        assert events[-1]["type"] == "done"
    assert _calls(spans, "summarizer") == []

    # Turn 6: once it is persisted the history holds twelve messages, ten visible and past the
    # trigger, and the six older than the window are folded at the end of the turn, after the
    # answer (ADR-0073), the retracted pair skipped.
    spans.clear()
    events = await post_turn(client, conversation_id=conversation, principal=principal, message=message)
    assert events[-1]["type"] == "done"
    summarizer_call, answer_call = _model_call(spans, "summarizer"), _model_call(spans, "reasoning")
    assert summarizer_call.start_time > answer_call.end_time
    [summarizer] = _contexts(spans, "summarizer")
    assert _count(summarizer, "window") == 4  # exchanges 2 and 3: the retracted pair is skipped
    [answer] = _contexts(spans, "answer")
    assert _count(answer, "summary") == 0
    assert not next(
        s for s in spans.get_finished_spans() if s.name == "node.assemble_context"
    ).attributes.get("context.summary.lazy")
    values = (await services.graph.aget_state(_thread(conversation))).values
    assert values["summary"] and values["summary_cursor"] == 6

    # Turn 7: the answer reads the summary and a full window; then, a summary existing, the
    # two messages that left the window are folded at the end of the turn.
    spans.clear()
    await post_turn(client, conversation_id=conversation, principal=principal, message="شكرًا.")
    [answer] = _contexts(spans, "answer")
    assert _count(answer, "summary") == 1 and _count(answer, "window") == 6
    [summarizer] = _contexts(spans, "summarizer")
    assert _count(summarizer, "summary") == 1 and _count(summarizer, "window") == 2
    assert _model_call(spans, "summarizer").start_time > _model_call(spans, "reasoning").end_time
    assert (await services.graph.aget_state(_thread(conversation))).values["summary_cursor"] == 8


async def test_the_lazy_fold_runs_before_the_answer_when_the_history_is_already_past_the_trigger(
    client, services, spans, litellm
):
    """The fallback (ADR-0073): history over the trigger at the start of a turn, as after a
    failed end-of-turn fold, is folded before the answer reads it."""
    counter = services.assembler.counter
    message = truncate(LONG, 1750, counter)
    conversation, principal = str(uuid.uuid4()), _principal()
    events = await post_turn(client, conversation_id=conversation, principal=principal)
    assert events[-1]["type"] == "done"
    # Five long exchanges written straight into the thread: past the trigger, never folded.
    history = [
        {"role": role, "content": message, "turn_id": f"t{i}"}
        for i in range(5)
        for role in ("user", "assistant")
    ]
    await services.graph.aupdate_state(_thread(conversation), {"messages": history}, as_node="write_memory")

    spans.clear()
    events = await post_turn(
        client, conversation_id=conversation, principal=principal, message="لخّص لي ما سبق."
    )
    assert events[-1]["type"] == "done"
    lazy, after = sorted(_calls(spans, "summarizer"), key=lambda span: span.start_time)
    answer_call = _model_call(spans, "reasoning")
    assert lazy.end_time < answer_call.start_time < answer_call.end_time < after.start_time
    [answer] = _contexts(spans, "answer")
    assert _count(answer, "summary") == 1  # the long window messages are cut to the answer budget
    assert next(s for s in spans.get_finished_spans() if s.name == "node.assemble_context").attributes[
        "context.summary.lazy"
    ]
    assert (await services.graph.aget_state(_thread(conversation))).values["summary_cursor"] == 8


async def test_the_debug_endpoint_is_off_unless_enabled(client, litellm):
    principal = _principal()
    events = await post_turn(client, conversation_id=str(uuid.uuid4()), principal=principal)
    response = await client.get(
        f"/v1/debug/context/{events[-1]['turn_id']}", headers=identity(principal["user_id"])
    )
    assert (response.status_code, response.json()["error"]["code"]) == (404, "not_found")


async def test_the_debug_endpoint_returns_a_turns_packages_to_its_owner_only(debug_client, litellm):
    owner, conversation = _principal(), str(uuid.uuid4())
    events = await post_turn(debug_client, conversation_id=conversation, principal=owner)
    url = f"/v1/debug/context/{events[-1]['turn_id']}"

    response = await debug_client.get(url, headers=identity(owner["user_id"]))
    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"] == conversation
    # general reads no package of its own, so none is assembled or recorded for it (C3).
    assert [p["node"] for p in body["packages"]] == ["answer", "memory_writer"]
    answer = body["packages"][0]["package"]
    assert answer["tier"] == "standard" and answer["accounting"]["total_tokens"] > 0
    assert (
        answer["blocks"][-1]["name"] == "user_message"
        and answer["blocks"][-1]["content"] == "ما عاصمة المملكة؟"
    )

    # Ownership is checked by the endpoint itself: another user, or the same user id in another tenant.
    for intruder in (identity("u-intruder"), identity(owner["user_id"], "tenant-b")):
        refused = await debug_client.get(url, headers=intruder)
        assert (refused.status_code, refused.json()["error"]["code"]) == (403, "conversation_forbidden")
    unknown = await debug_client.get(f"/v1/debug/context/{'0' * 32}", headers=identity(owner["user_id"]))
    assert (unknown.status_code, unknown.json()["error"]["code"]) == (404, "turn_not_found")
    assert (await debug_client.get(url, headers=identity(owner["user_id"], token=None))).status_code == 401
    assert (await debug_client.get(url, headers={"X-Internal-Token": TOKEN})).status_code == 400
