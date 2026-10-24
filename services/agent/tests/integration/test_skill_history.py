"""Conversation history keeps working through phase 06 (G9): conversations persisted before skills
existed (the phase 04 fixture of 30302d2 and the phase 05 one of 09464df, each serialized by that
code) reopen unchanged, with no `skill` on their replies, and a new turn with a skill continues
them."""

import json
import uuid
from pathlib import Path

import pytest

from app.api.runner import TurnRunner
from app.api.schemas import TurnRequest
from app.identity import Principal
from tests.integration.test_document_collections import _load_checkpoints

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "checkpoints"
CASES = [("phase04_docs_conversation.json", "30302d2"), ("phase05_docs_conversation.json", "09464df")]


async def _history(client, fixture: dict) -> list[dict]:
    response = await client.get(
        f"/v1/conversations/{fixture['conversation_id']}/messages",
        headers={"X-User-Id": fixture["user_id"], "X-Tenant-Id": fixture["tenant_id"],
                 "X-Internal-Token": "test-internal-token"},
    )
    assert response.status_code == 200, response.text
    return response.json()["messages"]


@pytest.mark.parametrize(("name", "commit"), CASES)
async def test_a_conversation_persisted_before_skills_reopens_unchanged(client, services, litellm, name, commit):
    fixture = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    assert fixture["generated_from"] == commit and "skill" not in fixture["state_keys"]
    await _load_checkpoints(services.db, fixture)
    messages = await _history(client, fixture)
    expected = [
        part
        for turn in fixture["turns"]
        for part in (("user", turn["question"], None), ("assistant", turn["answer"], turn["turn_id"]))
    ]
    assert [(m["role"], m["text"], m.get("turn_id")) for m in messages] == expected
    assert all(m.get("skill") is None for m in messages)
    replies = [m for m in messages if m["role"] == "assistant"]
    if commit == "09464df":
        # Since the conversation history (ADR-0193) a reply records its citations: they come back
        # exactly as the turn returned them.
        assert replies[0]["citations"] == fixture["citations"][0] and replies[1]["citations"] == []
    else:
        assert all(r["citations"] == [] for r in replies)


async def test_a_new_turn_with_a_skill_continues_a_phase_05_conversation(client, skill_services, litellm):
    fixture = json.loads((FIXTURES / "phase05_docs_conversation.json").read_text(encoding="utf-8"))
    fixture = {**fixture}
    await _load_checkpoints(skill_services.db, fixture)
    principal = Principal(
        user_id=fixture["user_id"], tenant_id=fixture["tenant_id"],
        permissions=("cap:general", "cap:docs", "skill:fixture_form"),
    )
    request = TurnRequest(conversation_id=fixture["conversation_id"], principal=principal,
                          message="شكرا مرة أخرى", locale="ar", skill="fixture_form")
    events = [e async for e in TurnRunner(skill_services).stream(request, f"req-g9-{uuid.uuid4().hex[:6]}")]
    assert events[-1].type == "done" and events[-1].data.state_digest.turn == 3
    assert events[-1].data.state_digest.skill == "fixture_form@1.0.0"
    state = await skill_services.graph.aget_state({"configurable": {"thread_id": fixture["conversation_id"]}})
    shown = [m for m in state.values["messages"] if m.type == "ai"]
    assert [m.additional_kwargs.get("skill") for m in shown] == [None, None, {"slug": "fixture_form", "version": "1.0.0"}]
