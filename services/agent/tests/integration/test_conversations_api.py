"""A user's conversations and a conversation's history (ADR-0192 to ADR-0194). Isolation
first: another user or another tenant never lists or reads a conversation; then the list's
order, paging and backfill, and what the history shows."""

import uuid

import pytest

from app.api.runner import TurnRunner
from app.identity import Principal
from tests.fixtures.http import TOKEN, identity, post_turn

pytestmark = pytest.mark.integration


def _user() -> str:
    return f"u-hist-{uuid.uuid4().hex[:8]}"


def _principal(user: str, tenant: str = "default") -> dict:
    return {"user_id": user, "tenant_id": tenant, "permissions": ["cap:general"]}


async def _list(client, user: str, tenant: str = "default", **params):
    response = await client.get("/v1/conversations", params=params, headers=identity(user, tenant))
    assert response.status_code == 200, response.text
    return response.json()


async def _messages(client, conversation: str, user: str, tenant: str = "default"):
    return await client.get(f"/v1/conversations/{conversation}/messages", headers=identity(user, tenant))


async def test_another_user_or_tenant_never_reads_or_lists_a_conversation(client, litellm):
    owner, intruder = _user(), _user()
    conversation = str(uuid.uuid4())
    events = await post_turn(
        client, conversation_id=conversation, principal=_principal(owner), message="سؤال خاص"
    )
    assert events[-1]["type"] == "done"

    for user, tenant in ((intruder, "default"), (owner, "tenant-b")):
        response = await _messages(client, conversation, user, tenant)
        assert response.status_code == 403 and response.json()["error"]["code"] == "conversation_forbidden"
        assert (await _list(client, user, tenant))["conversations"] == []
    assert [c["id"] for c in (await _list(client, owner))["conversations"]] == [conversation]


async def test_both_reads_need_the_internal_token(client):
    for url in ("/v1/conversations", f"/v1/conversations/{uuid.uuid4()}/messages"):
        response = await client.get(url, headers=identity(_user(), token=None))
        assert response.status_code == 401


async def test_a_finished_turn_lists_the_conversation_and_its_history(client, litellm):
    user = _user()
    conversation = str(uuid.uuid4())
    await post_turn(
        client, conversation_id=conversation, principal=_principal(user), message="ما عاصمة المملكة؟"
    )
    await post_turn(
        client, conversation_id=conversation, principal=_principal(user), message="وكم عدد سكانها؟"
    )

    listed = await _list(client, user)
    assert len(listed["conversations"]) == 1 and listed["next_before"] is None
    assert listed["conversations"][0]["title"] == "ما عاصمة المملكة؟"  # set once, by the first question

    body = (await _messages(client, conversation, user)).json()
    assert [(m["role"], m["text"]) for m in body["messages"]][::2] == [
        ("user", "ما عاصمة المملكة؟"),
        ("user", "وكم عدد سكانها؟"),
    ]
    replies = [m for m in body["messages"] if m["role"] == "assistant"]
    assert len(replies) == 2 and all(len(m["turn_id"]) == 32 and m["text"] for m in replies)


async def test_a_conversation_nobody_used_is_empty_and_uploads_alone_are_not_listed(client):
    user = _user()
    unused = str(uuid.uuid4())
    response = await _messages(client, unused, user)
    assert response.status_code == 200 and response.json()["messages"] == []
    upload = {
        "principal": {**_principal(user), "permissions": ["cap:general", "cap:docs"]},
        "name": "ملف.txt",
        "mime": "text/plain",
        "size": 4,
        "object_key": f"test/{unused}/{uuid.uuid4()}",
    }
    registered = await client.post(
        f"/v1/conversations/{unused}/attachments", json=upload, headers={"X-Internal-Token": TOKEN}
    )
    assert registered.status_code == 201
    assert (await _list(client, user))["conversations"] == []


async def test_newest_first_with_pages(client, litellm):
    user = _user()
    conversations = [str(uuid.uuid4()) for _ in range(3)]
    for i, conversation in enumerate(conversations):
        await post_turn(client, conversation_id=conversation, principal=_principal(user), message=f"سؤال {i}")

    first = await _list(client, user, limit=2)
    assert [c["id"] for c in first["conversations"]] == conversations[::-1][:2]
    assert first["next_before"] is not None
    second = await _list(client, user, limit=2, before=first["next_before"])
    assert [c["id"] for c in second["conversations"]] == [conversations[0]] and second["next_before"] is None

    # A new turn in the oldest conversation moves it to the top.
    await post_turn(client, conversation_id=conversations[0], principal=_principal(user), message="عودة")
    assert (await _list(client, user))["conversations"][0]["id"] == conversations[0]


async def test_rows_from_before_titles_are_filled_from_their_threads(client, services, litellm):
    user = _user()
    conversation = str(uuid.uuid4())
    await post_turn(client, conversation_id=conversation, principal=_principal(user), message="سؤال قديم")
    async with services.db.connection() as conn:
        await conn.execute(
            "UPDATE agent.conversation_owners SET title = NULL, last_turn_at = NULL "
            "WHERE conversation_id = %s",
            (conversation,),
        )
    assert [(c["id"], c["title"]) for c in (await _list(client, user))["conversations"]] == [
        (conversation, "سؤال قديم")
    ]
    async with services.db.connection() as conn:
        row = await (
            await conn.execute(
                "SELECT title, last_turn_at FROM agent.conversation_owners WHERE conversation_id = %s",
                (conversation,),
            )
        ).fetchone()
    assert row["title"] == "سؤال قديم" and row["last_turn_at"] is not None


async def test_the_history_names_only_this_conversations_files_and_hides_retracted_and_shown(
    client, services, litellm
):
    user = _user()
    conversation = str(uuid.uuid4())
    upload = {
        "principal": {**_principal(user), "permissions": ["cap:general", "cap:docs"]},
        "name": "لائحة.pdf",
        "mime": "application/pdf",
        "size": 10,
        "object_key": f"test/{conversation}/{uuid.uuid4()}",
    }
    own = (
        await client.post(
            f"/v1/conversations/{conversation}/attachments", json=upload, headers={"X-Internal-Token": TOKEN}
        )
    ).json()["id"]
    await post_turn(client, conversation_id=conversation, principal=_principal(user), message="سؤال سيُسحب")
    # A recorded exchange as persist_state writes it now (ADR-0193), with a foreign file id.
    config = {"configurable": {"thread_id": conversation}}
    await services.graph.aupdate_state(
        config,
        {
            "messages": [
                {"role": "user", "content": "لخص الملف", "attachment_ids": [own, str(uuid.uuid4())]},
                {
                    "role": "assistant",
                    "content": "الملخص [محجوب]",
                    "turn_id": uuid.uuid4().hex,
                    "shown_answer": "الملخص 0551234567",
                    "citations": [
                        {"id": own, "title": "لائحة.pdf", "source": "doc", "document_id": own, "pages": [3]}
                    ],
                },
            ]
        },
        as_node="write_memory",
    )
    first_turn = next(
        m.additional_kwargs["turn_id"]
        for m in (await services.graph.aget_state(config)).values["messages"]
        if m.type == "ai"
    )
    runner = TurnRunner(services)
    assert (
        await runner.retract(conversation, first_turn, Principal(**_principal(user)), "audit") == "retracted"
    )

    body = (await _messages(client, conversation, user)).json()
    assert [m["text"] for m in body["messages"]] == ["لخص الملف", "الملخص [محجوب]"]
    question, reply = body["messages"]
    assert question["attachments"] == [{"id": own, "name": "لائحة.pdf"}]
    assert reply["citations"][0]["pages"] == [3] and reply["citations"][0]["document_id"] == own
    assert "0551234567" not in str(body) and "shown_answer" not in str(body) and "سؤال سيُسحب" not in str(body)
