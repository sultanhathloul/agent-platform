"""Fixtures shared by the integration tests of phase 02."""

import dataclasses

import httpx
import pytest

from app.context.log import ContextLog


@pytest.fixture(scope="session")
def debug_services(services):
    """The session's services with every context package recorded and
    GET /v1/debug/context/{turn_id} enabled (ADR-0059)."""
    return dataclasses.replace(
        services,
        settings=services.settings.model_copy(update={"debug_context_enabled": True}),
        context_log=ContextLog(services.db, enabled=True),
    )


@pytest.fixture
async def debug_client(debug_services):
    from app.main import create_app

    app = create_app(start_background=False, services=debug_services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as http:
        yield http
