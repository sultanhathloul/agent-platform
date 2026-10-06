"""HTTP helpers for tests that call the agent API through an ASGI client."""

import json

TOKEN = "test-internal-token"


def identity(user_id: str, tenant_id: str = "default", *, token: str | None = TOKEN) -> dict[str, str]:
    """The headers the gateway sends on the agent's endpoints without a body (ADR-0059)."""
    headers = {"X-User-Id": user_id, "X-Tenant-Id": tenant_id}
    if token is not None:
        headers["X-Internal-Token"] = token
    return headers


def parse_sse(body: str) -> list[dict]:
    events = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        events.append(json.loads(fields["data"]))
    return events


async def post_turn(
    client, *, conversation_id: str, principal: dict, message: str = "ما عاصمة المملكة؟", locale: str = "ar"
) -> list[dict]:
    body = {"conversation_id": conversation_id, "principal": principal, "message": message, "locale": locale}
    response = await client.post("/v1/turns", json=body, headers={"X-Internal-Token": TOKEN})
    assert response.status_code == 200, response.text
    return parse_sse(response.text)
