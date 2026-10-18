"""The declarative capability runner (phase 05, E1, I12, ADR-0213), through a real turn with the
test-only capability `waypoint` (tests/fixtures/capabilities/waypoint: a manifest and prompts, no
Python). The extract call goes through Context Assembly and LiteLLM; its text is replaced
(ADR-0075). The steps call the probe's tool through the broker, the second fed by the first's
result; the model never fills a tool's arguments."""

import json
import uuid

import pytest

from app.api.runner import TurnRunner
from app.api.schemas import TurnRequest
from app.identity import Principal
from tests.fixtures.replace import replacing

pytestmark = pytest.mark.integration

PERMISSIONS = ("cap:general", "cap:waypoint", "cap:probe")


async def _turn(services, extract_replies, message="نقطة-اختبار: الدرعية"):
    swapped, gateway = replacing(services, {"extractor": extract_replies})
    principal = Principal(user_id=f"u-way-{uuid.uuid4().hex[:6]}", permissions=PERMISSIONS)
    conversation = str(uuid.uuid4())
    request = TurnRequest(conversation_id=conversation, principal=principal, message=message, locale="ar")
    events = [e async for e in TurnRunner(swapped).stream(request, "req-way")]
    state = await swapped.graph.aget_state({"configurable": {"thread_id": conversation}})
    [output] = [o for o in state.values.get("capability_outputs") or [] if o["capability"] == "waypoint"]
    return events, output, gateway


def _extracts(gateway) -> int:
    """Extractor calls before the answer; the memory writer's come after it."""
    calls = gateway.calls
    return (
        calls[: calls.index("reasoning")].count("extractor")
        if "reasoning" in calls
        else calls.count("extractor")
    )


def _tools(events):
    return [e.data.tool for e in events if e.type == "tool_start"]


async def test_steps_run_in_order_each_fed_by_the_last(probe_services, litellm):
    events, output, gateway = await _turn(probe_services, ['{"place": "الدرعية", "note": null}'])
    assert events[-1].type == "done"
    assert _tools(events) == ["probe_lookup", "probe_lookup"]
    assert "status=complete" in output["findings"]
    assert "first=lookup:الدرعية second=lookup:lookup:الدرعية" in output["findings"]
    assert _extracts(gateway) == 1
    assert output["cap_state"]["repairs"] == 0


async def test_an_invalid_extract_gets_one_repair_call(probe_services, litellm):
    events, output, gateway = await _turn(probe_services, ["ليس JSON", '{"place": "الدرعية", "note": null}'])
    assert _extracts(gateway) == 2 and output["cap_state"]["repairs"] == 1
    assert _tools(events) == ["probe_lookup", "probe_lookup"]


async def test_an_extract_still_invalid_after_the_repair_calls_no_tool(probe_services, litellm):
    events, output, gateway = await _turn(probe_services, ['{"place": 5}', '{"unexpected": "x"}'])
    assert _extracts(gateway) == 2
    assert _tools(events) == [] and "status=not_understood" in output["findings"]
    assert events[-1].type == "done"


async def test_a_missing_required_field_asks_and_calls_no_tool(probe_services, litellm):
    events, output, _ = await _turn(probe_services, ['{"place": null, "note": "x"}'])
    assert _tools(events) == []
    assert "status=missing" in output["findings"] and "missing=place" in output["findings"]


async def test_the_model_cannot_name_a_tool_argument(probe_services, litellm):
    """Whatever the extract holds beyond its schema is refused, and step arguments come only from
    the manifest's mapping: an extract naming `text` or another tool changes nothing."""
    events, output, _ = await _turn(
        probe_services,
        [
            '{"place": "الدرعية", "text": "forged", "tool": "docs_search"}',
            '{"place": "الدرعية", "note": null}',
        ],
    )
    assert _tools(events) == ["probe_lookup", "probe_lookup"]
    assert "forged" not in output["findings"]


# ------------------------------------------------------------- result shape, delimiters (ADR-0238, ADR-0239)

JUNCTION = ("cap:general", "cap:junction", "cap:probe")


async def _junction(services, query: str):
    swapped, _ = replacing(services, {"extractor": [json.dumps({"query": query}, ensure_ascii=False)]})
    principal = Principal(user_id=f"u-jn-{uuid.uuid4().hex[:6]}", permissions=JUNCTION)
    conversation = str(uuid.uuid4())
    request = TurnRequest(
        conversation_id=conversation, principal=principal, message="تقاطع-اختبار", locale="ar"
    )
    events = [e async for e in TurnRunner(swapped).stream(request, "req-jn")]
    state = await swapped.graph.aget_state({"configurable": {"thread_id": conversation}})
    [output] = [o for o in state.values.get("capability_outputs") or [] if o["capability"] == "junction"]
    return events, output["findings"]


def _delimited(findings: str, tool: str) -> list[dict]:
    """The step results the core rendered for `tool` inside the untrusted delimiters."""
    blocks = []
    marker = f'<untrusted source="{tool}">'
    start = findings.find(marker)
    while start >= 0:
        end = findings.index("</untrusted>", start)
        blocks.append(json.loads(findings[start + len(marker) : end]))
        start = findings.find(marker, end)
    return blocks


async def test_a_declared_result_list_of_another_shape_gives_the_status_and_candidates(
    probe_services, litellm
):
    events, findings = await _junction(probe_services, "عدة")
    assert _tools(events) == ["probe_find"]  # the second step never ran
    assert findings.startswith("status=ambiguous stopped_at=use candidates=الأول,الثاني,الثالث")
    events, findings = await _junction(probe_services, "لا شيء")
    assert _tools(events) == ["probe_find"] and findings.startswith("status=empty stopped_at=find")
    events, findings = await _junction(probe_services, "واحد")
    assert _tools(events) == ["probe_find", "probe_lookup"] and findings.startswith("status=complete")


async def test_step_results_are_delimited_by_the_core_whatever_the_template_says(probe_services, litellm):
    """The template renders no delimiter; the core puts every step result inside them."""
    _, findings = await _junction(probe_services, "واحد")
    assert "<untrusted" not in findings.split("\n", 1)[0]  # the template's own line
    [found] = _delimited(findings, "probe_find")
    assert found["options"]["items"] == [{"name": "الأول"}]
    [used] = _delimited(findings, "probe_lookup")
    assert used == {"text": "lookup:الأول"}
    _, ambiguous = await _junction(probe_services, "عدة")
    [listed] = _delimited(ambiguous, "probe_find")
    assert len(listed["options"]["items"]) == 4
