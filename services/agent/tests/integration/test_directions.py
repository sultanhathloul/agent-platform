"""The directions capability end to end (phase 05, E2): a real turn through the router rules, the
declarative runner, the broker and the platform's MCP client, against the reference routing server
(mcp_ref.routing) run in-process over Streamable HTTP. The extract call goes through Context
Assembly and LiteLLM; its text is replaced (ADR-0075).

Isolation first: a capability that does not declare the routing tools is refused before anything
reaches the server. Then the route's findings, the tool labels in tool_start, the content-free
audit entries, the injection place, ambiguity, and a dead server: the turn completes and says the
service is unavailable, and another user's general turn is untouched (G7)."""

import asyncio
import os
import uuid

import pytest
import uvicorn

from app.api.runner import TurnRunner
from app.api.schemas import TurnRequest
from app.identity import Principal
from tests.fixtures.mcp_server import free_port
from tests.fixtures.replace import replacing

pytestmark = pytest.mark.integration

PERMISSIONS = ("cap:general", "cap:directions")


class RoutingServer:
    def __init__(self) -> None:
        self.port = free_port()
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        from mcp_ref.routing.server import build

        app = build(host="127.0.0.1", port=self.port, delay_s=0, oversize=0).streamable_http_app()
        self._server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning")
        )
        self._task = asyncio.create_task(self._server.serve())
        while not self._server.started:
            await asyncio.sleep(0.05)

    async def stop(self) -> None:
        self._server.should_exit = True
        self._server.force_exit = True
        await asyncio.wait_for(self._task, 10)


@pytest.fixture(scope="module")
async def routing():
    server = RoutingServer()
    await server.start()
    yield server
    if server._server is not None and server._server.started and not server._server.should_exit:
        await server.stop()


@pytest.fixture(scope="module")
async def routing_services(settings, database_url, routing):
    from app.main import build_services, start_services, stop_services

    os.environ["MCP_ROUTING_URL"] = f"http://127.0.0.1:{routing.port}/mcp"
    try:
        services = await start_services(
            build_services(settings.model_copy(update={"database_url": database_url}))
        )
    finally:
        os.environ.pop("MCP_ROUTING_URL", None)
    assert await services.mcp.servers["routing"].connected.wait() is True
    assert services.mcp.servers["routing"].disabled == {}  # the shipped pins match the reference server
    yield services
    await stop_services(services)


async def _turn(
    services, message: str, extract: str | None, *, router: str = '{"capabilities": ["directions"]}'
):
    replies = {"router": router}
    if extract is not None:
        replies["extractor"] = extract
    swapped, _ = replacing(services, replies)
    principal = Principal(user_id=f"u-dir-{uuid.uuid4().hex[:6]}", permissions=PERMISSIONS)
    conversation = str(uuid.uuid4())
    request = TurnRequest(conversation_id=conversation, principal=principal, message=message, locale="ar")
    events = [e async for e in TurnRunner(swapped).stream(request, "req-dir")]
    state = await swapped.graph.aget_state({"configurable": {"thread_id": conversation}})
    outputs = {o["capability"]: o for o in state.values.get("capability_outputs") or []}
    return events, outputs


def test_the_manifest_is_declarative_and_holds_no_python():
    from pathlib import Path

    import yaml

    from app.capabilities.contract import Manifest

    path = Path(__file__).resolve().parents[2] / "app" / "capabilities" / "directions" / "manifest.yaml"
    manifest = Manifest.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    assert [s.tool for s in manifest.declarative.steps] == [
        "routing.geocode",
        "routing.geocode",
        "routing.route",
    ]
    assert manifest.permission == "cap:directions" and manifest.ui.mode == "auto"
    assert manifest.model.min_tier == "standard" and manifest.documents.collections == []
    assert [r.when for r in manifest.routing_hints.rules] == ["keyword_candidate"]
    assert not list(path.parent.rglob("*.py"))  # a manifest, prompts and evals (E2)


async def test_docs_calling_a_routing_tool_is_refused_before_the_server(routing_services, litellm):
    principal = Principal(user_id="u-dir-docs", permissions=("cap:docs", "cap:directions"))
    result = await routing_services.broker.execute(
        "routing.route", {"from": "kingdom_centre", "to": "airport"}, caller="docs", principal=principal
    )
    assert (result.status, result.error) == ("denied", "undeclared_tool")


async def test_a_route_reaches_the_findings_with_its_labels_and_a_content_free_audit(
    routing_services, litellm
):
    events, outputs = await _turn(
        routing_services,
        "كم يبعد بوليفارد سيتي عن برج المملكة بالسيارة؟",
        '{"origin": "برج المملكة", "destination": "بوليفارد سيتي", "profile": "car"}',
    )
    done = events[-1]
    assert done.type == "done" and done.data.state_digest.capabilities == ["directions"]
    starts = [e.data for e in events if e.type == "tool_start"]
    assert [s.tool for s in starts] == ["routing.geocode", "routing.geocode", "routing.route"]
    assert starts[0].label.ar == "البحث عن الموقع" and starts[2].label.ar == "حساب المسار"
    findings = outputs["directions"]["findings"]
    assert "12.7 كيلومتر" in findings and "19 دقيقة" in findings
    assert "الرياض جاليري" in findings  # a main step of the fixture route
    audit = done.data.tool_audit
    assert [(a.tool, a.server, a.status) for a in audit] == [
        ("routing.geocode", "routing", "ok"),
        ("routing.geocode", "routing", "ok"),
        ("routing.route", "routing", "ok"),
    ]
    assert all(len(a.args_hmac) == 64 and a.response_bytes > 0 for a in audit)
    assert "برج" not in done.data.model_dump_json(include={"tool_audit"})


async def test_the_injection_place_is_data_inside_the_untrusted_block(routing_services, litellm):
    events, outputs = await _turn(
        routing_services,
        "ودني ساحة الاختبار من برج المملكة",
        '{"origin": "برج المملكة", "destination": "ساحة الاختبار", "profile": "car"}',
    )
    findings = outputs["directions"]["findings"]
    assert "6.7 كيلومتر" in findings
    block = findings[findings.index("<untrusted") : findings.index("</untrusted>")]
    assert "عن الوجهة" in block  # whatever the description says stays inside the delimiters


async def test_an_ambiguous_place_lists_candidates_and_routes_nothing(routing_services, litellm):
    events, outputs = await _turn(
        routing_services,
        "كيف أروح المملكة من الدرعية؟",
        '{"origin": "الدرعية", "destination": "المملكة", "profile": null}',
    )
    tools = [e.data.tool for e in events if e.type == "tool_start"]
    assert tools == ["routing.geocode", "routing.geocode"]  # route never called
    findings = outputs["directions"]["findings"]
    assert "برج المملكة" in findings and "المملكة أرينا" in findings and "كيلومتر" not in findings


async def test_a_missing_origin_asks_and_calls_nothing(routing_services, litellm):
    events, outputs = await _turn(
        routing_services,
        "كيف أروح البوليفارد؟",
        '{"origin": null, "destination": "البوليفارد", "profile": null}',
    )
    assert not [e for e in events if e.type == "tool_start"]
    assert "من أين ينطلق" in outputs["directions"]["findings"]


async def test_a_dead_server_ends_in_an_unavailable_note_and_another_users_chat_goes_on(
    routing_services, routing, litellm
):
    await routing.stop()
    events, outputs = await _turn(
        routing_services,
        "كم يبعد المطار عن قصر المصمك؟",
        '{"origin": "قصر المصمك", "destination": "المطار", "profile": "car"}',
    )
    assert events[-1].type == "done"
    assert "غير متاحة" in outputs["directions"]["findings"]
    general, _ = await _turn(routing_services, "ما عاصمة المملكة العربية السعودية؟", None, router="{}")
    assert general[-1].type == "done" and general[-1].data.state_digest.capabilities == ["general"]
