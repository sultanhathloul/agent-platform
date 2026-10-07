"""DATA before storage (phase 03 B1) and the one-time purge (D4), through the seeded
policies `pii-block-in-memory` and `pii-redact-output` and the real presidio-analyzer.
Every number and address is fake (rule G3)."""

import json
import uuid

from app.api.runner import TurnRunner
from app.api.schemas import TurnRequest
from app.context.compress import truncate
from app.guardrails.checkpoints import Checkpoint
from app.guardrails.purge import purge
from app.guardrails.schema import Policy
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


async def _packages_using(services, item_ids: list[str]) -> int:
    async with services.db.connection() as conn:
        row = await (
            await conn.execute(
                "SELECT count(*) AS n FROM agent.context_packages WHERE memory_item_ids && %s::uuid[]",
                (item_ids,),
            )
        ).fetchone()
    return row["n"]


async def test_the_pre_guard_purge_counts_exactly_then_is_idempotent(services, litellm):
    """ADR-0112: exact counts of the rows changed; the dry run changes nothing, the summary
    included; a redact policy rewrites an item in place; the packages that held the old text go."""
    # The test database holds other tests' rows: settle them first, so the counts below are exact.
    await purge(services, dry_run=False)
    marker = f"zq{uuid.uuid4().hex[:8]}"
    redacting = Policy(
        id=f"purge-redact-{marker}",
        name="purge test redaction",
        scope="global",  # the purge evaluates global and tenant policies only; inert but for the marker
        checkpoints=[Checkpoint.DATA],
        checker="regex",
        config={"patterns": [{"name": "MARK", "pattern": marker}], "targets": ["memory_candidate"]},
        action="redact",
    )
    await services.policies.create(redacting, actor="test")
    try:
        principal = _principal()
        scope = MemoryScope.of(principal)
        vector = await services.embeddings.embed("x")

        async def add(text: str) -> str:
            return await services.memory.store.add(
                scope,
                kind="fact_about_user",
                text=text,
                confidence=0.9,
                embedding=vector,
                source_turn="t" * 32,
            )

        leaked, marked, clean = (
            await add(f"المستخدم بريده {EMAIL}"),
            await add(f"المستخدم رمزه {marker}"),
            await add("المستخدم يفضل الإيجاز"),
        )
        conversation = str(uuid.uuid4())
        assert (await _turn(services, principal, "ما عاصمة الأردن؟", conversation))[-1].type == "done"
        config = {"configurable": {"thread_id": conversation}}
        raw_summary = f"المستخدم أعطى رقمه {MOBILE}."
        await services.graph.aupdate_state(config, {"summary": raw_summary}, as_node="write_memory")
        async with services.db.connection() as conn:
            for item in (leaked, marked):
                await conn.execute(
                    "INSERT INTO agent.context_packages "
                    "(turn_id, conversation_id, node, package, memory_item_ids) "
                    "VALUES (%s, %s, 'answer', '{}'::jsonb, %s::uuid[])",
                    ("p" * 32, conversation, [item]),
                )

        dry = await purge(services, dry_run=True)
        expected = (1, 1, 1, 0)  # deleted, redacted, summaries redacted, summaries removed
        assert (
            dry.memory_items_deleted,
            dry.memory_items_redacted,
            dry.summaries_redacted,
            dry.summaries_removed,
        ) == expected
        assert {i.id for i in await services.memory.store.list_items(scope)} == {leaked, marked, clean}
        assert (await services.graph.aget_state(config)).values["summary"] == raw_summary  # untouched
        assert await _packages_using(services, [leaked, marked]) == 2

        applied = await purge(services, dry_run=False)
        assert (
            applied.memory_items_deleted,
            applied.memory_items_redacted,
            applied.summaries_redacted,
            applied.summaries_removed,
        ) == expected
        assert applied.memory_items_scanned == dry.memory_items_scanned
        items = {i.id: i.text for i in await services.memory.store.list_items(scope)}
        assert set(items) == {marked, clean} and items[marked] == "المستخدم رمزه [REDACTED:MARK]"
        assert await _packages_using(services, [leaked, marked]) == 0
        values = (await services.graph.aget_state(config)).values
        assert values["summary"] == "المستخدم أعطى رقمه [REDACTED:SA_MOBILE]."
        assert len(values["messages"]) == 2  # messages and history untouched (D4)

        again = await purge(services, dry_run=False)
        assert (
            again.memory_items_deleted,
            again.memory_items_redacted,
            again.summaries_redacted,
            again.summaries_removed,
        ) == (0, 0, 0, 0)
        # The conversation goes on after the purge.
        assert (await _turn(services, principal, "وما عملتها؟", conversation))[-1].type == "done"
    finally:
        await services.policies.set_enabled(redacting.id, False, actor="test")


async def test_the_purge_ignores_role_exceptions(services, litellm):
    """ADR-0112: a stored row is judged by global and tenant policies; a role exception that
    would let its holder's text through live does not spare it."""
    role = f"p-{uuid.uuid4().hex[:8]}"
    exempt = Policy(
        id=f"purge-exempt-{role}",
        name="purge test exemption",
        scope=f"role:{role}",
        checkpoints=[Checkpoint.DATA],
        checker="presidio",
        config={"entities": ["EMAIL_ADDRESS"], "targets": ["memory_candidate"]},
        action="allow",
        group="pii-memory",
    )
    await services.policies.create(exempt, actor="test")
    try:
        holder = _principal().model_copy(update={"policy_roles": (f"policy:{role}",)})
        item = await services.memory.store.add(
            MemoryScope.of(holder),
            kind="fact_about_user",
            text=f"المستخدم بريده {EMAIL}",
            confidence=0.9,
            embedding=await services.embeddings.embed("x"),
            source_turn="t" * 32,
        )
        await purge(services, dry_run=False)
        assert item not in {i.id for i in await services.memory.store.list_items(MemoryScope.of(holder))}
    finally:
        await services.policies.set_enabled(exempt.id, False, actor="test")
