"""The startup heap is frozen once the services start (ADR-0324): a full collection no longer
walks the registries, configuration and graph on the event loop."""

import gc

import pytest

pytestmark = pytest.mark.integration


async def test_started_services_leave_the_startup_heap_frozen(services):
    assert services.graph is not None and services.skills is not None
    assert gc.get_freeze_count() > 100_000  # the libraries, registries, configuration and graph
