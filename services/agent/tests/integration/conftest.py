"""Fixtures shared by the integration tests of phase 02."""

import dataclasses
import uuid

import httpx
import pytest

from app.context.log import ContextLog
from app.guardrails.schema import Policy


@pytest.fixture(scope="session")
def debug_services(services):
    """The session's services with every context package recorded and
    GET /v1/debug/context/{turn_id} enabled (ADR-0059)."""
    return dataclasses.replace(
        services,
        settings=services.settings.model_copy(update={"debug_context_enabled": True}),
        context_log=ContextLog(
            services.db, enabled=True, retention_days=services.settings.debug_context_retention_days
        ),
    )


@pytest.fixture
async def debug_client(debug_services):
    from app.main import create_app

    app = create_app(start_background=False, services=debug_services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as http:
        yield http


@pytest.fixture
async def role(services, request):
    """A policy role of its own; every policy created through `add` is switched off afterwards.
    A test on probe_services runs a second service instance, a second "process" whose
    store would see the change only when its pub/sub reload lands, so it is reloaded."""
    name = f"t-{uuid.uuid4().hex[:10]}"
    created: list[str] = []
    stores = [services.policies]
    if "probe_services" in request.fixturenames:
        stores.append(request.getfixturevalue("probe_services").policies)

    async def add(**fields) -> Policy:
        base = {"name_ar": "سياسة اختبار", "name_en": "test", "scope": f"role:{name}", "severity": "medium"}
        policy = Policy.model_validate({"id": f"{name}-{len(created)}", **base, **fields})
        await services.policies.create(policy, actor="test")
        created.append(policy.id)
        for store in stores[1:]:
            await store.load()
        return policy

    yield name, add
    for pid in created:
        await services.policies.set_enabled(pid, False, actor="test")
    for store in stores[1:]:
        await store.load()
