"""DATA before storage (phase 03 B1) and the one-time purge (D4), through the seeded
policies `pii-block-in-memory` and `pii-redact-output` and the real presidio-analyzer.
Every number and address is fake (rule G3)."""

import json
import uuid

from app.api.runner import TurnRunner
from app.api.schemas import TurnRequest
from app.context.compress import truncate
from app.guardrails.purge import purge
from app.identity import Principal
from app.memory.types import MemoryScope
from tests.fixtures.replace import replacing

MOBILE = "0501234567"
EMAIL = "test.user@example.org"


def _principal() -> Principal:
    return Principal(user_id=f"u-{uuid.uuid4().hex[:8]}", permissions=("cap:general",))


async def _turn(services, principal: Principal, message: str, conversation: str | None = None):
    request = TurnRequest(
        conversation_id=conversation or str(uuid.uuid4()), principal=principal, message=message, locale="ar"
    )
    return [e async for e in TurnRunner(services).stream(request, f"req-{uuid.uuid4().hex[:6]}")]


async def test_a_memory_candidate_with_a_mobile_number_is_dropped_without_a_message(services, spans, litellm):
    principal = _principal()
    # Both candidates are grounded in the user's own words (ADR-0064); one carries the number.
    extraction = json.dumps(
        [
            {"kind": "fact_about_user", "text": f"المستخدم رقم جواله {MOBILE}", "confidence": 0.9},
            {"kind": "preference", "text": "المستخدم يفضل الإجابات المختصرة", "confidence": 0.9},
        ],
        ensure_ascii=False,
    )
    replaced, _ = replacing(services, {"extractor": extraction})
    events = await _turn(replaced, principal, f"رقم جوالي {MOBILE} وأفضل الإجابات المختصرة. ما عاصمة الأردن؟")
    assert events[-1].type == "done"
    assert not [e for e in events if e.type == "error"]

    stored = await services.memory.store.list_items(MemoryScope.of(principal))
    assert [item.text for item in stored] == ["المستخدم يفضل الإجابات المختصرة"]
    assert not any(MOBILE in item.text for item in stored)

    blocks = [
        g
        for g in events[-1].data.guard_events
        if g.policy_id == "pii-block-in-memory" and g.action == "block"
    ]
    assert len(blocks) == 1 and blocks[0].target == "memory_candidate" and blocks[0].entities == ["SA_MOBILE"]
    write = next(s for s in spans.get_finished_spans() if s.name == "node.write_memory")
    assert write.attributes["memory.guard_dropped"] == 1 and write.attributes["memory.stored"] == 1


async def test_a_new_summary_is_redacted_before_it_is_stored(services, litellm):
    counter, trigger = services.assembler.counter, services.assembler.config.summary.trigger_tokens
    message = truncate("الرياض عاصمة المملكة العربية السعودية وأكبر مدنها. " * 400, 1750, counter)
    assert 4 * counter.count(message) + 400 <= trigger < 5 * counter.count(message)
    summary = f"يسأل المستخدم عن الرياض، ورقم جواله {MOBILE} وبريده {EMAIL}."
    replaced, gateway = replacing(services, {"summarizer": summary})
    principal, conversation = _principal(), str(uuid.uuid4())
    for _ in range(6):
        events = await _turn(replaced, principal, message, conversation)
        assert events[-1].type == "done"
    assert "summarizer" in gateway.calls

    values = (await services.graph.aget_state({"configurable": {"thread_id": conversation}})).values
    assert values["summary"] == (
        "يسأل المستخدم عن الرياض، ورقم جواله [REDACTED:SA_MOBILE] وبريده [REDACTED:EMAIL_ADDRESS]."
    )
    redactions = [
        g
        for g in events[-1].data.guard_events
        if g.policy_id == "pii-redact-output" and g.target == "summary"
    ]
    assert redactions and redactions[0].action == "redact"


async def test_the_pre_guard_purge_is_a_dry_run_then_idempotent(services, litellm):
    principal = _principal()
    scope = MemoryScope.of(principal)
    vector = await services.embeddings.embed("x")
    leaked = await services.memory.store.add(
        scope,
        kind="fact_about_user",
        text=f"المستخدم بريده {EMAIL}",
        confidence=0.9,
        embedding=vector,
        source_turn="t" * 32,
    )
    clean = await services.memory.store.add(
        scope,
        kind="preference",
        text="المستخدم يفضل الإيجاز",
        confidence=0.9,
        embedding=vector,
        source_turn="t" * 32,
    )
    conversation = str(uuid.uuid4())
    assert (await _turn(services, principal, "ما عاصمة الأردن؟", conversation))[-1].type == "done"
    config = {"configurable": {"thread_id": conversation}}
    await services.graph.aupdate_state(
        config, {"summary": f"المستخدم أعطى رقمه {MOBILE}."}, as_node="write_memory"
    )

    dry = await purge(services, dry_run=True)
    assert dry.memory_items_deleted >= 1 and dry.summaries_redacted >= 1
    assert {i.id for i in await services.memory.store.list_items(scope)} == {leaked, clean}

    applied = await purge(services, dry_run=False)
    assert (applied.memory_items_scanned, applied.memory_items_deleted, applied.summaries_redacted) == (
        dry.memory_items_scanned,
        dry.memory_items_deleted,
        dry.summaries_redacted,
    )
    assert [i.id for i in await services.memory.store.list_items(scope)] == [clean]
    values = (await services.graph.aget_state(config)).values
    assert values["summary"] == "المستخدم أعطى رقمه [REDACTED:SA_MOBILE]."
    assert len(values["messages"]) == 2  # messages and history untouched (D4)

    again = await purge(services, dry_run=False)
    assert (again.memory_items_deleted, again.summaries_redacted, again.summaries_removed) == (0, 0, 0)
    # The conversation goes on after the purge.
    assert (await _turn(services, principal, "وما عملتها؟", conversation))[-1].type == "done"
