"""End-to-end turns through the real path: API → graph → Context Assembly →
models.call → LiteLLM (mock aliases, ADR-0007)."""

import json
import uuid

import pytest

pytestmark = pytest.mark.integration

MOCK_ANSWER = "هذه إجابة تجريبية من النموذج الوهمي. This is a mock answer."
TOKEN = {"X-Internal-Token": "test-internal-token"}
USER = {
    "user_id": "u-1",
    "tenant_id": "default",
    "permissions": ["cap:general"],
    "display_name": "سارة",
    "locale": "ar",
}


def parse_sse(body: str) -> list[dict]:
    events = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        event = json.loads(fields["data"])
        assert fields["event"] == event["type"] and int(fields["id"]) == event["seq"]
        events.append(event)
    return events


async def post_turn(
    client,
    *,
    conversation_id=None,
    message="ما عاصمة المملكة العربية السعودية؟",
    principal=USER,
    headers=TOKEN,
    **extra,
):
    body = {
        "conversation_id": conversation_id or str(uuid.uuid4()),
        "principal": principal,
        "message": message,
        "locale": "ar",
        **extra,
    }
    return await client.post("/v1/turns", json=body, headers={**headers, "X-Request-Id": "req-1"})


async def test_general_turn_streams_tokens_then_done(client, litellm):
    response = await post_turn(client)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)

    types = [e["type"] for e in events]
    assert types[0] == "tier" and types[-1] == "done"
    assert [e["seq"] for e in events] == list(range(len(events)))
    assert {e["request_id"] for e in events} == {"req-1"}
    assert len({e["turn_id"] for e in events}) == 1

    assert events[0]["data"]["tier"] == "standard"  # general's floor (README §19.4, ADR-0024)
    tokens = [e["data"]["text"] for e in events if e["type"] == "token"]
    assert len(tokens) >= 2
    assert "".join(tokens) == MOCK_ANSWER

    done = events[-1]["data"]
    assert done["answer"] == MOCK_ANSWER
    assert done["state_digest"] == {"turn": 1, "capabilities": ["general"]}
    assert done["usage"]["tokens_out"] > 0 and done["usage"]["tokens_in"] > 0


async def test_history_carries_into_the_next_turn(client, services, litellm):
    conversation = str(uuid.uuid4())
    await post_turn(client, conversation_id=conversation)
    second = parse_sse(
        (await post_turn(client, conversation_id=conversation, message="وما أكبر مدنها؟")).text
    )
    assert second[-1]["data"]["state_digest"]["turn"] == 2

    snapshot = await services.graph.aget_state({"configurable": {"thread_id": conversation}})
    assert [m.type for m in snapshot.values["messages"]] == ["human", "ai", "human", "ai"]
    assert snapshot.values["messages"][2].content == "وما أكبر مدنها؟"


async def test_thread_of_another_user_or_tenant_is_rejected(client, services, litellm):
    conversation = str(uuid.uuid4())
    await post_turn(client, conversation_id=conversation)

    for intruder in ({**USER, "user_id": "u-2"}, {**USER, "tenant_id": "tenant-b"}):
        response = await post_turn(
            client, conversation_id=conversation, principal=intruder, message="ماذا سألتُ قبل قليل؟"
        )
        events = parse_sse(response.text)
        assert [e["type"] for e in events] == ["error"]
        assert events[0]["data"]["code"] == "conversation_forbidden"
        assert events[0]["data"]["message_ar"] and events[0]["data"]["message_en"]

    snapshot = await services.graph.aget_state({"configurable": {"thread_id": conversation}})
    assert (snapshot.values["user_id"], snapshot.values["tenant_id"]) == ("u-1", "default")
    assert [m.type for m in snapshot.values["messages"]] == ["human", "ai"]


async def retract(client, conversation_id, turn_id, *, principal=USER, headers=TOKEN):
    return await client.post(
        f"/v1/conversations/{conversation_id}/turns/{turn_id}/retract",
        json={"principal": principal, "reason": "audit_unavailable"},
        headers=headers,
    )


async def test_retracted_exchange_is_skipped_by_the_next_turn(client, services, spans, litellm):
    """ADR-0048, ADR-0076: the reply and its question leave the context as a pair."""
    conversation = str(uuid.uuid4())
    first = parse_sse((await post_turn(client, conversation_id=conversation)).text)
    turn_id = first[-1]["turn_id"]

    assert (await retract(client, conversation, turn_id)).status_code == 204
    assert (await retract(client, conversation, turn_id)).status_code == 204  # idempotent
    snapshot = await services.graph.aget_state({"configurable": {"thread_id": conversation}})
    reply = snapshot.values["messages"][1]
    assert reply.additional_kwargs["retracted"] is True
    assert reply.additional_kwargs["retract_reason"] == "audit_unavailable"

    spans.clear()
    await post_turn(client, conversation_id=conversation, message="وما أكبر مدنها؟")
    answer_contexts = [
        json.loads(s.attributes["langfuse.observation.metadata.context"])
        for s in spans.get_finished_spans()
        if s.name == "context.assemble" and s.attributes.get("context.node") == "answer"
    ]
    assert len(answer_contexts) == 1
    assert [b for b in answer_contexts[0]["blocks"] if b["name"] == "window"] == []


async def test_retract_is_refused_for_another_owner_an_unknown_turn_or_no_token(client, litellm):
    conversation = str(uuid.uuid4())
    turn_id = parse_sse((await post_turn(client, conversation_id=conversation)).text)[-1]["turn_id"]

    other = await retract(client, conversation, turn_id, principal={**USER, "user_id": "u-2"})
    assert (other.status_code, other.json()["error"]["code"]) == (403, "conversation_forbidden")
    unknown = await retract(client, conversation, "0" * 32)
    assert (unknown.status_code, unknown.json()["error"]["code"]) == (404, "turn_not_found")
    assert (await retract(client, str(uuid.uuid4()), turn_id)).status_code == 404
    assert (await retract(client, conversation, turn_id, headers={})).status_code == 401
    assert (await retract(client, conversation.upper(), turn_id)).status_code == 400
    assert (await retract(client, conversation, "not-a-turn")).status_code == 400


async def test_trace_has_context_block_token_counts(client, spans, litellm):
    # A user new to this session: no memory yet, and turn 1 has an empty digest.
    principal = {**USER, "user_id": f"u-{uuid.uuid4().hex[:8]}"}
    parse_sse((await post_turn(client, principal=principal)).text)
    by_name = {}
    for span in spans.get_finished_spans():
        by_name.setdefault(span.name, []).append(span)

    turn = by_name["turn"][0]
    assert turn.attributes["langfuse.trace.name"] == "turn"
    assert turn.attributes["langfuse.user.id"] != principal["user_id"]  # hashed

    assembled = {s.attributes["context.node"]: s for s in by_name["context.assemble"]}
    # general's own package goes through the resolver and gets a real budget (ADR-0038)
    general = assembled["capability.general"]
    assert general.attributes["context.tier"] == "standard" and general.attributes["context.budget_limit"] > 0

    assemble = assembled["answer"]
    for block in ("system", "user_message"):
        assert assemble.attributes[f"context.block.{block}.tokens"] > 0
    block_tokens = json.loads(assemble.attributes["langfuse.observation.metadata.block_tokens"])
    assert set(block_tokens) == {"system", "user_message"}  # empty digest, summary and memory omitted
    assert assemble.parent is not None and assemble.context.trace_id == turn.context.trace_id

    generation = by_name["model.call"][0]
    assert generation.attributes["langfuse.observation.type"] == "generation"
    assert json.loads(generation.attributes["model.tier_decision"])["final"] == "standard"  # ADR-0024
    assert generation.attributes["model.temperature"] == 0.2  # reasoning role (ADR-0032)
    assert {f"node.{n}" for n in ("route", "run_capabilities", "answer", "validate")} <= set(by_name)
    assert "capability.general" in by_name
    checkpoints = {s.attributes["guard.checkpoint"] for s in by_name["guard.check"]}
    assert checkpoints == {"INPUT", "TOOL_LIST", "MODEL_TIER", "OUTPUT", "DATA"}


async def test_missing_internal_token_is_rejected(client):
    response = await post_turn(client, headers={})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert response.json()["error"]["message_ar"]


async def test_invalid_body_returns_envelope(client):
    response = await client.post("/v1/turns", json={"message": ""}, headers=TOKEN)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


async def test_unknown_max_tier_is_a_contract_error(client):
    # ADR-0049: principal.max_tier is an enum of the known tiers; nothing runs for an unknown one.
    response = await post_turn(client, principal={**USER, "max_tier": "turbo"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


async def test_user_without_any_capability_gets_an_error_event(client):
    events = parse_sse((await post_turn(client, principal={**USER, "permissions": []})).text)
    assert [e["type"] for e in events] == ["error"]
    assert events[0]["data"]["code"] == "no_capability_available"
    assert events[0]["data"]["message_ar"] and events[0]["data"]["message_en"]


async def test_unpermitted_kbs_are_filtered_again(client, services, litellm):
    conversation = str(uuid.uuid4())
    principal = {**USER, "permissions": ["cap:general", "kb:moe:read"]}
    await post_turn(client, conversation_id=conversation, principal=principal, selected_kbs=["moe", "moh"])
    snapshot = await services.graph.aget_state({"configurable": {"thread_id": conversation}})
    assert snapshot.values["selected_kbs"] == ["moe"]


async def test_health_lists_enabled_capabilities(client):
    response = await client.get("/v1/health")
    assert response.json() == {"status": "ok", "capabilities": ["general"]}
