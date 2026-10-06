"""Conversation ownership in the agent (ADR-0041, ADR-0050): the runner binds a
conversation to its first caller and refuses anyone else before the graph writes
anything; the identity node keeps its own check behind it."""

import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest

from app.api.ownership import Owner
from app.api.runner import TurnRunner
from app.api.schemas import TurnRequest
from app.errors import TurnError
from app.graph.nodes.identity import load_identity_and_state
from app.identity import Principal

pytestmark = pytest.mark.integration

TOKEN = {"X-Internal-Token": "test-internal-token"}


def _principal(user_id: str = "u-1", tenant_id: str = "default") -> dict:
    return {"user_id": user_id, "tenant_id": tenant_id, "permissions": ["cap:general"]}


async def _turn(client, conversation_id: str, principal: dict, message: str = "ما عاصمة المملكة؟"):
    body = {"conversation_id": conversation_id, "principal": principal, "message": message, "locale": "ar"}
    return await client.post("/v1/turns", json=body, headers=TOKEN)


def _events(response) -> list[dict]:
    return [
        json.loads(line[len("data: ") :]) for line in response.text.splitlines() if line.startswith("data: ")
    ]


async def _checkpoints(services, conversation_id: str) -> list[str]:
    config = {"configurable": {"thread_id": conversation_id}}
    return [
        c.config["configurable"]["checkpoint_id"] async for c in services.graph.aget_state_history(config)
    ]


async def test_a_refused_turn_writes_nothing_to_the_thread(client, services, litellm):
    conversation = str(uuid.uuid4())
    await _turn(client, conversation, _principal())
    config = {"configurable": {"thread_id": conversation}}
    checkpoints = await _checkpoints(services, conversation)
    values = (await services.graph.aget_state(config)).values

    for intruder in (_principal(user_id="u-2"), _principal(tenant_id="tenant-b")):
        events = _events(await _turn(client, conversation, intruder, message="ماذا سأل صاحب المحادثة؟"))
        assert [(e["type"], e["data"]["code"]) for e in events] == [("error", "conversation_forbidden")]

    assert await _checkpoints(services, conversation) == checkpoints
    assert (await services.graph.aget_state(config)).values == values


async def test_concurrent_first_turns_bind_one_owner(client, services, litellm):
    """Overlapping turns on one conversation are serialized by the turn lock (D6): one
    runs, an overlapping one is refused with 409 turn_in_flight, and one that comes after
    the winner finished is refused as conversation_forbidden. Either way one owner."""
    conversation = str(uuid.uuid4())
    responses = await asyncio.gather(
        *(_turn(client, conversation, _principal(user_id=f"u-race-{i}")) for i in range(4))
    )
    winners = [r for r in responses if r.status_code == 200 and _events(r)[-1]["type"] == "done"]
    assert len(winners) == 1
    for response in responses:
        if response in winners:
            continue
        if response.status_code == 409:
            assert response.json()["error"]["code"] == "turn_in_flight"
        else:
            assert [e["data"]["code"] for e in _events(response)] == ["conversation_forbidden"]
    snapshot = await services.graph.aget_state({"configurable": {"thread_id": conversation}})
    assert snapshot.values["user_id"] == json.loads(winners[0].request.content)["principal"]["user_id"]


async def test_concurrent_first_bindings_agree_on_one_owner(services):
    """The binding itself is atomic, whatever serializes the turns around it (ADR-0050)."""
    conversation = str(uuid.uuid4())

    async def nobody():
        return None

    callers = [Owner(f"u-bind-{i}", "default") for i in range(8)]
    owners = await asyncio.gather(*(services.owners.bind_or_get(conversation, c, nobody) for c in callers))
    assert len(set(owners)) == 1 and owners[0] in callers


async def test_an_owner_recorded_in_the_thread_wins_over_a_lost_binding(services, litellm):
    # e.g. an owner row lost from agent.conversation_owners while the checkpoint survived
    owner = TurnRequest(
        conversation_id=str(uuid.uuid4()),
        principal=Principal(user_id="u-1", permissions=("cap:general",)),
        message="ما عاصمة المملكة؟",
        locale="ar",
    )
    assert [e.type async for e in TurnRunner(services).stream(owner, "req-owner")][-1] == "done"

    async with services.db.connection() as conn:
        await conn.execute(
            "DELETE FROM agent.conversation_owners WHERE conversation_id = %s", (owner.conversation_id,)
        )
    intruder = owner.model_copy(update={"principal": Principal(user_id="u-2", permissions=("cap:general",))})
    events = [e async for e in TurnRunner(services).stream(intruder, "req-intruder")]
    assert [(e.type, e.data.code) for e in events] == [("error", "conversation_forbidden")]


@pytest.mark.parametrize(
    "conversation_id",
    [str(uuid.uuid4()).upper(), "not-a-uuid", "a" * 64, ""],
    ids=["upper-case", "not-a-uuid", "long", "empty"],
)
async def test_conversation_id_must_be_a_lower_case_uuid(client, conversation_id):
    response = await _turn(client, conversation_id, _principal())
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


async def test_the_identity_node_still_refuses_another_owner():
    runtime = SimpleNamespace(context=SimpleNamespace(principal=Principal(user_id="u-2"), turn_id="t"))
    with pytest.raises(TurnError) as info:
        await load_identity_and_state({"user_id": "u-1", "tenant_id": "default"}, runtime)
    assert info.value.code == "conversation_forbidden"
