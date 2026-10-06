"""Retention (D5, phase 03 B3): no memory, summary or extractor content goes to Langfuse,
only ids, token counts and content hashes; recorded context packages expire, name the
memory items they used, and go when such an item is deleted."""

import json
import uuid

from app.api.runner import TurnRunner
from app.api.schemas import TurnRequest
from app.identity import Principal
from app.memory.types import MemoryScope
from tests.fixtures.http import identity

SECRET_PROJECT = "مشروع النخلة الذهبية"
SECRET_SUMMARY = "ناقش المستخدم خطة سرية لمشروع الواحة الزرقاء"


def _principal() -> Principal:
    return Principal(user_id=f"u-{uuid.uuid4().hex[:8]}", permissions=("cap:general",))


async def _turn(services, principal, message, conversation=None):
    request = TurnRequest(
        conversation_id=conversation or str(uuid.uuid4()), principal=principal, message=message, locale="ar"
    )
    events = [e async for e in TurnRunner(services).stream(request, "req-retention")]
    assert events[-1].type == "done", events[-1]
    return request.conversation_id, events


def _calls(spans, role: str):
    return [
        s
        for s in spans.get_finished_spans()
        if s.name == "model.call" and s.attributes.get("model.role") == role
    ]


async def _seed_memory(services, principal) -> str:
    vector = await services.embeddings.embed(SECRET_PROJECT)  # the mock embeds every text alike
    return await services.memory.store.add(
        MemoryScope.of(principal),
        kind="project_context",
        text=f"المستخدم يعمل على {SECRET_PROJECT}",
        confidence=0.9,
        embedding=vector,
        source_turn="s" * 32,
    )


async def test_memory_and_summary_text_never_reach_a_generation_span(services, spans, litellm):
    principal = _principal()
    item = await _seed_memory(services, principal)
    conversation, _ = await _turn(services, principal, "ما عاصمة الأردن؟")
    await services.graph.aupdate_state(
        {"configurable": {"thread_id": conversation}}, {"summary": SECRET_SUMMARY}, as_node="write_memory"
    )
    spans.clear()
    await _turn(services, principal, f"أخبرني عن {SECRET_PROJECT}", conversation)

    [answer] = _calls(spans, "reasoning")
    shown = answer.attributes["langfuse.observation.input"]
    assert SECRET_PROJECT in shown  # the user's own message is content, captured as before
    messages = json.loads(shown)
    data = messages[-1]["content"]
    assert "النخلة الذهبية" not in data.split(f"أخبرني عن {SECRET_PROJECT}")[0]
    assert "الواحة الزرقاء" not in shown
    assert "[memory:" in data and item in data and "[summary:" in data and "sha256" in data
    assert answer.attributes["langfuse.observation.metadata.input_sha256"]


async def test_extractor_and_summarizer_calls_carry_no_content(services, spans, litellm):
    principal = _principal()
    await _turn(services, principal, "أفضّل أن تكون الإجابات مختصرة جدًا.")
    [extractor] = _calls(spans, "extractor")
    assert "langfuse.observation.input" not in extractor.attributes
    assert "langfuse.observation.output" not in extractor.attributes
    assert len(extractor.attributes["langfuse.observation.metadata.input_sha256"]) == 64
    assert len(extractor.attributes["langfuse.observation.metadata.output_sha256"]) == 64
    assert extractor.attributes["gen_ai.usage.output_tokens"] > 0


async def test_packages_name_their_memory_items_and_go_with_them(debug_client, debug_services, litellm):
    principal = _principal()
    item = await _seed_memory(debug_services, principal)
    _, events = await _turn(debug_services, principal, f"أخبرني عن {SECRET_PROJECT}")
    turn_id = events[-1].turn_id
    async with debug_services.db.connection() as conn:
        rows = await (
            await conn.execute(
                "SELECT node, memory_item_ids, expires_at > now() + interval '6 days' AS long_lived "
                "FROM agent.context_packages WHERE turn_id = %s",
                (turn_id,),
            )
        ).fetchall()
    answer = next(r for r in rows if r["node"] == "answer")
    assert [str(i) for i in answer["memory_item_ids"]] == [item] and answer["long_lived"]

    headers = identity(principal.user_id)
    assert (await debug_client.get(f"/v1/debug/context/{turn_id}", headers=headers)).status_code == 200
    assert await debug_services.memory.store.delete(MemoryScope.of(principal), item)
    async with debug_services.db.connection() as conn:
        left = await (
            await conn.execute(
                "SELECT node FROM agent.context_packages WHERE turn_id = %s AND %s = ANY(memory_item_ids)",
                (turn_id, item),
            )
        ).fetchall()
    assert left == []


async def test_expired_packages_are_not_served_and_are_purged(debug_client, debug_services, litellm):
    principal = _principal()
    _, events = await _turn(debug_services, principal, "ما عاصمة الأردن؟")
    turn_id = events[-1].turn_id
    headers = identity(principal.user_id)
    assert (await debug_client.get(f"/v1/debug/context/{turn_id}", headers=headers)).status_code == 200
    async with debug_services.db.connection() as conn:
        await conn.execute(
            "UPDATE agent.context_packages SET expires_at = now() - interval '1 minute' WHERE turn_id = %s",
            (turn_id,),
        )
    expired = await debug_client.get(f"/v1/debug/context/{turn_id}", headers=headers)
    assert (expired.status_code, expired.json()["error"]["code"]) == (404, "turn_not_found")
    assert await debug_services.context_log.purge_expired() >= 1
    async with debug_services.db.connection() as conn:
        count = await (
            await conn.execute(
                "SELECT count(*) AS n FROM agent.context_packages WHERE turn_id = %s", (turn_id,)
            )
        ).fetchone()
    assert count["n"] == 0


async def test_retention_days_come_from_the_settings(debug_services):
    assert (
        debug_services.context_log.retention_days == debug_services.settings.debug_context_retention_days == 7
    )
