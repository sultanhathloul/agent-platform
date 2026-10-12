"""The agent's attachment API (phase 04, A2; ADR-0129). Isolation first (G6): another
user, by the owner's attachment id, gets 404 on status; by the owner's conversation id, 403;
no role or header reaches another user's file. There is no delete (owner decision S4). Then
registration, the file limit and the type check."""

import uuid

import pytest

from app.graph.router import parse_decision
from tests.fixtures.http import TOKEN, identity

pytestmark = pytest.mark.integration


def _principal(user: str) -> dict:
    return {"user_id": user, "tenant_id": "default", "permissions": ["cap:general", "cap:docs"]}


async def _register(client, conversation: str, user: str, name: str = "لائحة.pdf"):
    body = {
        "principal": _principal(user),
        "name": name,
        "mime": "application/pdf",
        "size": 10,
        "object_key": f"test/{conversation}/{uuid.uuid4()}",
    }
    return await client.post(
        f"/v1/conversations/{conversation}/attachments", json=body, headers={"X-Internal-Token": TOKEN}
    )


async def test_another_user_never_reads_or_lists_an_attachment(client):
    owner, intruder = f"u-own-{uuid.uuid4().hex[:6]}", f"u-int-{uuid.uuid4().hex[:6]}"
    conversation = str(uuid.uuid4())
    created = await _register(client, conversation, owner)
    assert created.status_code == 201, created.text
    attachment = created.json()
    assert attachment["status"] == "pending" and attachment["kind"] == "pdf"
    path = f"/v1/conversations/{conversation}/attachments"

    # By the owner's conversation id: 403 on every operation.
    for url in (path, f"{path}/{attachment['id']}"):
        response = await client.get(url, headers=identity(intruder))
        assert response.status_code == 403 and response.json()["error"]["code"] == "conversation_forbidden"
    registered = await _register(client, conversation, intruder)
    assert registered.status_code == 403

    # By the owner's attachment id, in the intruder's own conversation: 404, never the file.
    own = str(uuid.uuid4())
    await _register(client, own, intruder, "ملفي.txt")
    response = await client.get(
        f"/v1/conversations/{own}/attachments/{attachment['id']}", headers=identity(intruder)
    )
    assert response.status_code == 404 and response.json()["error"]["code"] == "attachment_not_found"
    listed = await client.get(f"/v1/conversations/{own}/attachments", headers=identity(intruder))
    assert [a["name"] for a in listed.json()["attachments"]] == ["ملفي.txt"]
    # Another tenant with the same user id is another user.
    response = await client.get(f"{path}/{attachment['id']}", headers=identity(owner, "t2"))
    assert response.status_code == 403

    # The owner still has it, and cannot delete it: the product deletes no file (S4).
    assert (await client.get(f"{path}/{attachment['id']}", headers=identity(owner))).status_code == 200
    assert (await client.delete(f"{path}/{attachment['id']}", headers=identity(owner))).status_code == 405
    assert (await client.get(f"{path}/{attachment['id']}", headers=identity(owner))).status_code == 200


async def test_the_internal_token_is_required(client):
    conversation = str(uuid.uuid4())
    response = await client.get(
        f"/v1/conversations/{conversation}/attachments", headers=identity("u-x", token=None)
    )
    assert response.status_code == 401


async def test_the_file_limit_and_the_type_are_enforced(client, services):
    user = f"u-lim-{uuid.uuid4().hex[:6]}"
    conversation = str(uuid.uuid4())
    limit = services.attachments.limits.max_files_per_conversation
    for index in range(limit):
        assert (await _register(client, conversation, user, f"ملف {index}.md")).status_code == 201
    over = await _register(client, conversation, user, "زائد.md")
    assert over.status_code == 409 and over.json()["error"]["code"] == "attachment_limit"
    assert over.json()["error"]["message_ar"]
    wrong = await _register(client, str(uuid.uuid4()), user, "برنامج.exe")
    assert wrong.status_code == 415 and wrong.json()["error"]["code"] == "file_type_unsupported"


async def test_an_upper_case_conversation_id_is_refused(client):
    response = await _register(client, str(uuid.uuid4()).upper(), "u-case")
    assert response.status_code == 400


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"capabilities": ["docs"], "complexity": "low", "risk": "low"}', ["docs"]),
        ('```json\n{"capabilities": ["general", "docs"]}\n```', ["general", "docs"]),
        ('{"capabilities": ["sql"]}', None),  # outside the candidates
        ("docs", None),
        ('{"capabilities": "docs"}', None),
    ],
)
def test_the_router_answer_is_parsed_strictly(text, expected):
    decision = parse_decision(text, ["docs", "general"])
    assert (decision.capabilities if decision else None) == expected
