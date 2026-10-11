"""Long-term memory through the agent (README §8, ADR-0058): a turn writes memory for
its (tenant, user) and a later conversation of that user retrieves it; no
retrieval, list, delete or switch crosses (tenant_id, user_id); a user whose
memory is off gets nothing written or retrieved; a turn a guard flagged writes
nothing."""

import dataclasses
import json
import uuid

import pytest

from app.api.runner import TurnRunner
from app.api.schemas import TurnRequest
from app.guardrails.checkpoints import Checkpoint
from app.guardrails.engine import NO_CONTEXT, GuardEngine
from app.guardrails.schema import GuardEventRecord, GuardOutcome
from app.identity import Principal
from app.memory.types import MemoryScope
from tests.fixtures.http import TOKEN, identity, post_turn

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def fixed_services(debug_services):
    """The debug services with memory embedded through `embed-fixed`, the mock alias that
    gives every text the same vector: these tests need every item to match every query
    (ADR-0143). The `embed` alias ranks by shared words instead."""
    from app.context.assembler import ContextAssembler
    from app.memory.retriever import MemoryRetriever
    from app.memory.service import MemoryService
    from app.memory.writer import MemoryWriter
    from app.models.embeddings import EmbeddingGateway

    embeddings = EmbeddingGateway(debug_services.settings, debug_services.models_config, alias="embed-fixed")
    old = debug_services.memory
    memory = MemoryService(
        config=old.config,
        store=old.store,
        retriever=MemoryRetriever(old.store, embeddings, old.config),
        writer=MemoryWriter(old.store, embeddings, old.config),
    )
    assembler = ContextAssembler(
        debug_services.assembler.config,
        debug_services.prompts,
        debug_services.assembler.counter,
        memory=memory.retriever,
    )
    return dataclasses.replace(debug_services, embeddings=embeddings, memory=memory, assembler=assembler)


@pytest.fixture
async def fixed_client(fixed_services):
    import httpx

    from app.main import create_app

    app = create_app(start_background=False, services=fixed_services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as http:
        yield http


VICTIM_MEMORY = "VICTIM-MEMORY-5b21: يعمل المستخدم على ملف تحقيق سري"


def _principal(tenant_id: str = "default") -> dict:
    return {"user_id": f"u-{uuid.uuid4().hex[:8]}", "tenant_id": tenant_id, "permissions": ["cap:general"]}


def _scope(principal: dict) -> MemoryScope:
    return MemoryScope(tenant_id=principal["tenant_id"], user_id=principal["user_id"])


async def _seed(services, scope: MemoryScope, text: str) -> str:
    vector = await services.embeddings.embed(text)
    return await services.memory.store.add(
        scope, kind="project_context", text=text, confidence=0.95, embedding=vector, source_turn="seed"
    )


async def _turn(client, principal: dict, message: str = "بماذا تنصحني اليوم؟") -> tuple[str, dict]:
    """Runs a turn in a new conversation and returns its turn id and recorded packages."""
    events = await post_turn(client, conversation_id=str(uuid.uuid4()), principal=principal, message=message)
    assert events[-1]["type"] == "done", events[-1]
    turn_id = events[-1]["turn_id"]
    response = await client.get(
        f"/v1/debug/context/{turn_id}", headers=identity(principal["user_id"], principal["tenant_id"])
    )
    assert response.status_code == 200
    return turn_id, response.json()


def _answer_blocks(recorded: dict) -> list[dict]:
    return next(p["package"]["blocks"] for p in recorded["packages"] if p["node"] == "answer")


async def _items(client, principal: dict) -> dict:
    response = await client.get("/v1/memory", headers=identity(principal["user_id"], principal["tenant_id"]))
    assert response.status_code == 200
    return response.json()


async def test_a_turn_writes_memory_that_a_later_conversation_retrieves(
    fixed_client, fixed_services, litellm
):
    principal = _principal()
    turn_id, first = await _turn(fixed_client, principal, "أفضّل أن تكون الإجابات مختصرة.")
    assert "memory" not in [b["name"] for b in _answer_blocks(first)]
    listed = await _items(fixed_client, principal)
    assert listed["enabled"] is True
    [item] = listed["items"]
    assert item["kind"] == "preference" and item["text"]  # the mock extractor's candidate
    async with fixed_services.db.connection() as conn:
        cursor = await conn.execute("SELECT source_turn FROM agent.memory_items WHERE id = %s", (item["id"],))
        assert (await cursor.fetchone())["source_turn"] == turn_id

    _, second = await _turn(fixed_client, principal)
    [memory] = [b for b in _answer_blocks(second) if b["name"] == "memory"]
    assert item["text"] in memory["content"] and memory["meta"]["items"][0]["id"] == item["id"]
    # The second turn extracted the same candidate again: a duplicate, not a second item.
    assert [i["id"] for i in (await _items(fixed_client, principal))["items"]] == [item["id"]]


async def test_memory_never_crosses_tenant_and_user(fixed_client, fixed_services, litellm):
    victim = _principal(tenant_id="tenant-a")
    victim_item = await _seed(fixed_services, _scope(victim), VICTIM_MEMORY)
    _, own = await _turn(fixed_client, victim)
    assert VICTIM_MEMORY in json.dumps(
        _answer_blocks(own), ensure_ascii=False
    )  # the victim's own turn sees it

    for attacker in (_principal(tenant_id="tenant-a"), {**victim, "tenant_id": "tenant-b"}):
        headers = identity(attacker["user_id"], attacker["tenant_id"])
        _, recorded = await _turn(fixed_client, attacker)
        assert VICTIM_MEMORY not in json.dumps(recorded, ensure_ascii=False)
        listed = await _items(fixed_client, attacker)
        assert VICTIM_MEMORY not in json.dumps(listed, ensure_ascii=False)
        deleted = await fixed_client.delete(f"/v1/memory/{victim_item}", headers=headers)
        assert (deleted.status_code, deleted.json()["error"]["code"]) == (404, "memory_not_found")
        switched = await fixed_client.put("/v1/memory/settings", json={"enabled": False}, headers=headers)
        assert switched.json() == {"enabled": False}

    after = await _items(fixed_client, victim)
    assert after["enabled"] is True and victim_item in [i["id"] for i in after["items"]]


async def test_with_memory_off_nothing_is_written_or_retrieved(fixed_client, fixed_services, spans, litellm):
    principal = _principal()
    headers = identity(principal["user_id"])
    seeded = await _seed(fixed_services, _scope(principal), "يفضّل المستخدم عرض الأرقام في جداول.")
    assert (
        await fixed_client.put("/v1/memory/settings", json={"enabled": False}, headers=headers)
    ).json() == {"enabled": False}

    spans.clear()
    _, recorded = await _turn(fixed_client, principal)
    assert [p["node"] for p in recorded["packages"]] == ["capability.general", "answer"]  # no extractor call
    assert "memory" not in [b["name"] for b in _answer_blocks(recorded)]
    finished = spans.get_finished_spans()
    assert not [
        s for s in finished if s.name == "model.embed" or s.attributes.get("model.role") == "extractor"
    ]
    write = next(s for s in finished if s.name == "node.write_memory")
    assert write.attributes["memory.write.outcome"] == "disabled"
    listed = await _items(fixed_client, principal)
    assert listed["enabled"] is False and [i["id"] for i in listed["items"]] == [seeded]

    await fixed_client.put("/v1/memory/settings", json={"enabled": True}, headers=headers)
    _, again = await _turn(fixed_client, principal)
    assert "memory" in [b["name"] for b in _answer_blocks(again)]


class _WarningGuard(GuardEngine):
    """Flags every INPUT with a `warn` policy event."""

    async def evaluate(self, checkpoint, payload, *, principal=None, context=NO_CONTEXT):
        if checkpoint != Checkpoint.INPUT:
            return await super().evaluate(checkpoint, payload, principal=principal, context=context)
        event = GuardEventRecord(checkpoint=checkpoint, policy_id="test-warn", action="warn")
        return GuardOutcome(action="warn", payload=payload, events=[event])


async def test_a_turn_a_guard_flagged_writes_no_memory(services, spans, litellm):
    principal = Principal(user_id=f"u-{uuid.uuid4().hex[:8]}", permissions=("cap:general",))
    request = TurnRequest(
        conversation_id=str(uuid.uuid4()), principal=principal, message="أفضّل الإجابات المختصرة.", locale="ar"
    )
    flagged = dataclasses.replace(services, guard=_WarningGuard())
    events = [e async for e in TurnRunner(flagged).stream(request, "req-flagged")]
    assert events[-1].type == "done"
    finished = spans.get_finished_spans()
    assert next(s for s in finished if s.name == "node.write_memory").attributes["memory.write.outcome"] == (
        "guard_flagged"
    )
    assert not [s for s in finished if s.attributes.get("model.role") == "extractor"]
    assert await services.memory.store.list_items(MemoryScope.of(principal)) == []
    assert next(s for s in finished if s.name == "turn").attributes["memory.write_ms"] >= 0


async def test_a_user_deletes_their_own_item_and_the_api_checks_token_and_identity(client, services, litellm):
    principal = _principal()
    item = await _seed(services, _scope(principal), "يعمل المستخدم على التقرير السنوي.")
    headers = identity(principal["user_id"])
    assert (await client.delete(f"/v1/memory/{item}", headers=headers)).status_code == 204
    assert (await _items(client, principal))["items"] == []
    assert (await client.delete(f"/v1/memory/{item}", headers=headers)).status_code == 404
    assert (
        await client.get("/v1/memory", headers=identity(principal["user_id"], token=None))
    ).status_code == 401
    assert (await client.get("/v1/memory", headers={"X-Internal-Token": TOKEN})).status_code == 400
    assert (await client.delete("/v1/memory/not-a-uuid", headers=headers)).status_code == 400
    invalid = await client.put("/v1/memory/settings", json={"enabled": "not a boolean"}, headers=headers)
    assert (invalid.status_code, invalid.json()["error"]["code"]) == (400, "invalid_request")


async def _retract(client, conversation: str, turn_id: str, principal: dict):
    return await client.post(
        f"/v1/conversations/{conversation}/turns/{turn_id}/retract",
        json={"principal": principal, "reason": "audit_unavailable"},
        headers={"X-Internal-Token": TOKEN},
    )


async def _source_turns(services, scope: MemoryScope) -> list[str]:
    async with services.db.connection() as conn:
        cursor = await conn.execute(
            "SELECT source_turn FROM agent.memory_items "
            "WHERE tenant_id = %s AND user_id = %s ORDER BY source_turn",
            (scope.tenant_id, scope.user_id),
        )
        return [row["source_turn"] for row in await cursor.fetchall()]


async def _reply_retracted(services, conversation: str) -> bool:
    values = (await services.graph.aget_state({"configurable": {"thread_id": conversation}})).values
    return bool(values["messages"][-1].additional_kwargs.get("retracted"))


async def test_retracting_a_reply_deletes_the_memory_that_turn_wrote(client, services, litellm):
    """A withdrawn reply leaves no memory behind (ADR-0071): the retract deletes the
    turn's items, only that turn's and only the owner's."""
    principal, conversation = _principal(), str(uuid.uuid4())
    scope = _scope(principal)
    older = await services.memory.store.add(
        scope,
        kind="preference",
        text="المستخدم يفضّل الجداول.",
        confidence=0.9,
        embedding=[0.0, 1.0],
        source_turn="earlier-turn",
    )
    events = await post_turn(
        client, conversation_id=conversation, principal=principal, message="أفضّل أن تكون الإجابات مختصرة."
    )
    turn_id = events[-1]["turn_id"]
    other = _principal()
    await services.memory.store.add(
        _scope(other),
        kind="preference",
        text="المستخدم يفضّل الإيجاز.",
        confidence=0.9,
        embedding=[1.0, 0.0],
        source_turn=turn_id,
    )
    assert await _source_turns(services, scope) == sorted(["earlier-turn", turn_id])

    assert (await _retract(client, conversation, turn_id, principal)).status_code == 204
    assert await _reply_retracted(services, conversation)
    assert await _source_turns(services, scope) == ["earlier-turn"]
    assert [i.id for i in await services.memory.store.list_items(scope)] == [older]
    assert await _source_turns(services, _scope(other)) == [turn_id]  # another user's row is untouched
    assert (await _retract(client, conversation, turn_id, principal)).status_code == 204  # idempotent


async def test_the_retract_flag_and_the_memory_delete_commit_together(client, services, litellm, monkeypatch):
    principal, conversation = _principal(), str(uuid.uuid4())
    events = await post_turn(
        client, conversation_id=conversation, principal=principal, message="أفضّل أن تكون الإجابات مختصرة."
    )
    turn_id = events[-1]["turn_id"]
    assert await _source_turns(services, _scope(principal)) == [turn_id]

    store = services.memory.store
    delete = store.delete_from_turn

    async def failing(scope, source_turn, *, conn):
        await delete(scope, source_turn, conn=conn)
        raise RuntimeError("the connection dropped after the delete")

    monkeypatch.setattr(store, "delete_from_turn", failing)
    with pytest.raises(RuntimeError):
        await TurnRunner(services).retract(conversation, turn_id, Principal(**principal), "audit_unavailable")
    assert not await _reply_retracted(services, conversation)  # the flag rolled back with the delete
    assert await _source_turns(services, _scope(principal)) == [turn_id]

    monkeypatch.setattr(store, "delete_from_turn", delete)
    assert (await _retract(client, conversation, turn_id, principal)).status_code == 204
    assert await _reply_retracted(services, conversation)
    assert await _source_turns(services, _scope(principal)) == []
