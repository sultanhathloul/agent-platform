"""Retention (D5, phase 03 B3): no memory, summary or extractor content goes to Langfuse,
only ids, token counts and keyed content hashes (ADR-0110), whatever span carries it;
recorded context packages expire, name the memory items they used, and go when such an
item is deleted."""

import json
import uuid

from app.api.runner import TurnRunner
from app.api.schemas import TurnRequest
from app.context.compress import truncate
from app.guardrails.checkpoints import Checkpoint
from app.guardrails.schema import Policy
from app.identity import Principal
from app.memory.types import MemoryScope
from tests.fixtures.http import identity
from tests.fixtures.replace import replacing

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


MEMORY_TEXT = f"المستخدم يعمل على {SECRET_PROJECT}"
VERDICT = json.dumps({"violates": False, "confidence": 0.9, "category": "none"})


def _span_text(spans) -> str:
    """Every string attribute of every exported span, as one text to search."""
    return "\n".join(
        value
        for span in spans.get_finished_spans()
        for value in (span.attributes or {}).values()
        if isinstance(value, str)
    )


def _assert_content_free(call) -> None:
    assert "langfuse.observation.input" not in call.attributes
    assert "langfuse.observation.output" not in call.attributes
    assert call.attributes["model.content_free"] is True
    assert len(call.attributes["langfuse.observation.metadata.input_hmac"]) == 64
    assert len(call.attributes["langfuse.observation.metadata.output_hmac"]) == 64


async def test_memory_and_summary_text_never_reach_any_span(services, spans, litellm):
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
    data = json.loads(shown)[-1]["content"]
    assert "[memory:" in data and item in data and "[summary:" in data and "hmac" in data
    exported = _span_text(spans)
    assert "المستخدم يعمل على" not in exported and "الواحة الزرقاء" not in exported
    assert answer.attributes["langfuse.observation.metadata.input_hmac"]


async def _policy(services, *, target: str, role: str) -> str:
    """A DATA llm_judge policy on `target`, held by a policy role of its own (ADR-0107)."""
    pid = f"judge-{target.replace('_', '-')}-{uuid.uuid4().hex[:8]}"
    policy = Policy(
        id=pid,
        name_ar="سياسة اختبار",
        name_en="judge on stored text",
        scope=f"role:{role}",
        checkpoints=[Checkpoint.DATA],
        checker="llm_judge",
        config={"topic_description": "أي موضوع سري", "targets": [target]},
        action="log_only",
    )
    await services.policies.create(policy, actor="test")
    return pid


async def test_a_judged_memory_candidate_reaches_no_span_and_no_package(debug_services, spans, litellm):
    role = f"j-{uuid.uuid4().hex[:8]}"
    pid = await _policy(debug_services, target="memory_candidate", role=role)
    candidate = json.dumps(
        [{"kind": "project_context", "text": MEMORY_TEXT, "confidence": 0.9}], ensure_ascii=False
    )
    replaced, gateway = replacing(debug_services, {"extractor": candidate, "judge": VERDICT})
    principal = _principal().model_copy(update={"policy_roles": (f"policy:{role}",)})
    try:
        spans.clear()
        _, events = await _turn(replaced, principal, f"أنا أعمل على {SECRET_PROJECT}.")
        assert "judge" in gateway.calls
        [judge] = _calls(spans, "judge")
        _assert_content_free(judge)
        [extractor] = _calls(spans, "extractor")
        _assert_content_free(extractor)
        assert "المستخدم يعمل على" not in _span_text(spans)
        async with debug_services.db.connection() as conn:
            nodes = {
                r["node"]
                for r in await (
                    await conn.execute(
                        "SELECT node FROM agent.context_packages WHERE turn_id = %s", (events[-1].turn_id,)
                    )
                ).fetchall()
            }
        assert "answer" in nodes and "guard_judge" not in nodes
    finally:
        await debug_services.policies.set_enabled(pid, False, actor="test")


async def test_the_extractor_summarizer_and_a_judged_summary_carry_no_content(debug_services, spans, litellm):
    """Both halves exercised: the extractor on every turn, the summarizer once the history
    passes its trigger; a judge on the summary target is content-free too (D5, ADR-0110)."""
    counter, trigger = (
        debug_services.assembler.counter,
        debug_services.assembler.config.summary.trigger_tokens,
    )
    message = truncate("الرياض عاصمة المملكة العربية السعودية وأكبر مدنها. " * 400, 1750, counter)
    assert 4 * counter.count(message) + 400 <= trigger < 5 * counter.count(message)
    role = f"j-{uuid.uuid4().hex[:8]}"
    pid = await _policy(debug_services, target="summary", role=role)
    replaced, gateway = replacing(debug_services, {"summarizer": SECRET_SUMMARY, "judge": VERDICT})
    principal = _principal().model_copy(update={"policy_roles": (f"policy:{role}",)})
    conversation = str(uuid.uuid4())
    try:
        spans.clear()
        for _ in range(6):
            conversation, _ = await _turn(replaced, principal, message, conversation)
        assert {"summarizer", "extractor", "judge"} <= set(gateway.calls)
        for role_name in ("summarizer", "extractor", "judge"):
            calls = _calls(spans, role_name)
            assert calls, role_name
            for call in calls:
                _assert_content_free(call)
        assert "الواحة الزرقاء" not in _span_text(spans)
        async with debug_services.db.connection() as conn:
            judged = await (
                await conn.execute(
                    "SELECT count(*) AS n FROM agent.context_packages "
                    "WHERE conversation_id = %s AND node = %s",
                    (conversation, "guard_judge"),
                )
            ).fetchone()
        assert judged["n"] == 0
    finally:
        await debug_services.policies.set_enabled(pid, False, actor="test")


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
