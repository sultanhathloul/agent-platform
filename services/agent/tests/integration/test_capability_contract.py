"""The capability contract end to end (ADR-0038): a test-only capability outside
app/capabilities uses models.call and the broker through its RuntimeContext,
and its events reach the client while it is still running."""

import asyncio
import importlib
import uuid

import pytest

from app.api.runner import TurnRunner
from app.api.schemas import TurnRequest
from app.identity import Principal

pytestmark = pytest.mark.integration

PROBE = importlib.import_module("tests.fixtures.capabilities.probe.graph")


async def test_probe_uses_models_and_broker_and_streams_while_running(probe_services, litellm):
    request = TurnRequest(
        conversation_id=str(uuid.uuid4()),
        principal=Principal(user_id="u-probe", permissions=("cap:general", "cap:probe")),
        message="probe-capability: ما عاصمة المملكة؟",
        locale="ar",
    )
    PROBE.GATE = asyncio.Event()
    events = []
    try:
        async for event in TurnRunner(probe_services).stream(request, "req-probe"):
            events.append(event)
            if event.type == "tool_end":
                # The capability is blocked until this event reaches us; delivery
                # after the capability finished would time it out.
                PROBE.GATE.set()
    finally:
        PROBE.GATE = None

    types = [e.type for e in events]
    assert types[:3] == ["tool_start", "tool_end", "tier"], types
    tool_end = events[1].data
    assert (tool_end.tool, tool_end.capability, tool_end.status) == ("probe_lookup", "probe", "ok")
    step_call = events[2].data
    assert (step_call.step, step_call.tier) == ("draft", "fast")  # step override below the standard floor
    assert types[-1] == "done", types
    done = events[-1].data
    assert done.state_digest.capabilities == ["probe"]
    assert done.usage.tokens_out > 0

    snapshot = await probe_services.graph.aget_state({"configurable": {"thread_id": request.conversation_id}})
    assert snapshot.values["cap_state"]["cap.probe"] == {"runs": 1}
    assert snapshot.values["capability_outputs"][0]["findings"].startswith("lookup:probe-capability")


def test_no_core_file_names_the_probe():
    from pathlib import Path

    app = Path(__file__).resolve().parents[2] / "app"
    files = [p for p in app.rglob("*") if p.is_file() and p.suffix in {".py", ".yaml"}]
    offenders = [p for p in files if "probe" in p.read_text(encoding="utf-8")]
    assert offenders == []
