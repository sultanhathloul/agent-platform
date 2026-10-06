"""State survives a restart (README §2, ADR-0055): a fresh agent — new services,
connection pool and checkpointer on the same database — continues the
conversation, keeps its owner binding and the user's memory."""

import uuid

import pytest

from app.api.runner import TurnRunner
from app.api.schemas import TurnRequest
from app.identity import Principal
from app.main import build_services, start_services, stop_services
from app.memory.types import MemoryScope

pytestmark = pytest.mark.integration


async def test_checkpoint_owners_and_memory_survive_a_restart(services, litellm):
    owner = Principal(user_id=f"u-{uuid.uuid4().hex[:8]}", permissions=("cap:general",))
    # The message holds the words of the mock extraction, so the item is grounded and stored (ADR-0064).
    message = "أفضّل الإجابات المختصرة. ما عاصمة المملكة؟"
    first = TurnRequest(conversation_id=str(uuid.uuid4()), principal=owner, message=message, locale="ar")
    assert [e.type async for e in TurnRunner(services).stream(first, "req-before")][-1] == "done"
    remembered = [item.id for item in await services.memory.store.list_items(MemoryScope.of(owner))]
    assert remembered

    restarted = await start_services(build_services(services.settings))
    try:
        assert restarted.db is not services.db and restarted.graph is not services.graph
        second = first.model_copy(update={"message": "وما أكبر مدنها؟"})
        events = [e async for e in TurnRunner(restarted).stream(second, "req-after")]
        assert events[-1].type == "done" and events[-1].data.state_digest.turn == 2
        values = (
            await restarted.graph.aget_state({"configurable": {"thread_id": first.conversation_id}})
        ).values
        assert [m.type for m in values["messages"]] == ["human", "ai", "human", "ai"]

        async with restarted.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT user_id FROM agent.conversation_owners WHERE conversation_id = %s",
                (first.conversation_id,),
            )
            assert (await cursor.fetchone())["user_id"] == owner.user_id
        intruder = first.model_copy(
            update={"principal": Principal(user_id="u-intruder", permissions=("cap:general",))}
        )
        refused = [e async for e in TurnRunner(restarted).stream(intruder, "req-intruder")]
        assert [(e.type, e.data.code) for e in refused] == [("error", "conversation_forbidden")]

        assert [
            item.id for item in await restarted.memory.store.list_items(MemoryScope.of(owner))
        ] == remembered
    finally:
        await stop_services(restarted)
