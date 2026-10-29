"""Report turns end to end (reports PR, D1–D4, I1, I3–I6; ADR-0378 to ADR-0384): the composer's picker
or the router chooses the form, the report writer's page streams through the guards as artifact events, its
figures are checked, its sources section appended, its version stored in MinIO and the database, read by
its owner only, and a follow-up makes the next version of the same report. The models' texts are replaced
after the real calls through Context Assembly and LiteLLM (ADR-0075)."""

import dataclasses
import json
import re
import uuid

import httpx
import pytest

from app.api.runner import TurnRunner
from app.api.schemas import TurnRequest
from app.identity import Principal
from tests.fixtures.replace import ReplacingGateway

pytestmark = pytest.mark.integration

PAGE = """```html
<!doctype html><html lang="ar" dir="rtl"><head><title>لوحة النمو</title>
<script src="https://cdn.example.com/chart.js"></script></head>
<body><main class="kit-page"><header class="kit-header"><h1>لوحة النمو</h1></header>
<div class="kit-kpis"><div class="kit-kpi"><div class="kit-kpi-label">النمو</div><div class="kit-kpi-value">3.6</div></div>
<div class="kit-kpi"><div class="kit-kpi-label">العام السابق</div><div class="kit-kpi-value">3.1</div></div></div>
<p>للتواصل: 0501234567</p>
<div class="kit-chart" data-chart="g"></div>
<script type="application/json" id="report-data">{"charts":{"g":{"type":"line","labels":["2030","2031"],
"datasets":[{"label":"النمو","data":[3.1,3.6]}]}}}</script></main></body></html>
```"""
EDITED = PAGE.replace("<h1>لوحة النمو</h1>", "<h1>لوحة النمو باللون الأخضر</h1>").replace(
    "<title>لوحة النمو</title>", "<title>لوحة النمو</title><style>:root{--kit-accent:#0f766e}</style>"
)
ROUTE_REPORT = '{"capabilities": ["general"], "skill": null, "output": "report", "complexity": "low", "risk": "low"}'
ROUTE_TEXT = '{"capabilities": ["general"], "skill": null, "output": "text", "complexity": "low", "risk": "low"}'


class CapturingGateway(ReplacingGateway):
    """Also keeps the blocks each call's package held, by role."""

    def __init__(self, inner, replies):
        super().__init__(inner, replies)
        self.blocks: dict[str, list[list[str]]] = {}
        self.systems: dict[str, list[str]] = {}

    async def call(self, decision, package, *, on_token=None, temperature=None):
        self.blocks.setdefault(decision.role, []).append([b.name for b in package.blocks])
        self.systems.setdefault(decision.role, []).append(next((b.content for b in package.blocks if b.name == "system"), ""))
        return await super().call(decision, package, on_token=on_token, temperature=temperature)


def _principal() -> Principal:
    return Principal(user_id=f"u-rep-{uuid.uuid4().hex[:6]}", permissions=("cap:general",))


async def _turn(services, principal, message, *, conversation=None, output=None, locale="ar"):
    request = TurnRequest(
        conversation_id=conversation or str(uuid.uuid4()), principal=principal, message=message,
        locale=locale, output=output,
    )
    runner = TurnRunner(services)
    admission = await runner.admit(request)
    events = [e async for e in runner.stream(request, f"req-{uuid.uuid4().hex[:6]}", admission=admission)]
    return events, request.conversation_id, admission


def _of(events, kind):
    return [e for e in events if e.type == kind]


@pytest.fixture
def reports(services):
    def make(replies):
        gateway = CapturingGateway(services.gateway, replies)
        return dataclasses.replace(services, gateway=gateway), gateway

    return make


async def test_a_picked_report_streams_checked_marked_and_stored_and_a_follow_up_is_version_2(reports):
    services, gateway = reports({"report_writer": [PAGE, EDITED], "router": ROUTE_REPORT})
    principal = _principal()
    events, conversation, admission = await _turn(
        services, principal, "اعمل لي لوحة مؤشرات عن نمو الاقتصاد 3.6 في 2031", output="report"
    )
    assert admission.profile == "report" and admission.deadline_s == 300
    assert events[-1].type == "done", events[-1]
    start, deltas, done = _of(events, "artifact_start"), _of(events, "artifact_delta"), _of(events, "artifact_done")
    assert len(start) == 1 and deltas and len(done) == 1
    assert start[0].data.title == "لوحة النمو" and start[0].data.id == done[0].data.id
    assert gateway.blocks["report_writer"][0][:2] == ["system", "report_kit"]
    assert "skill" not in gateway.blocks["report_writer"][0] and "report_current" not in gateway.blocks["report_writer"][0]
    final = events[-1].data
    assert final.state_digest.output == "report" and final.artifact.version == 1
    assert "لوحة النمو" in final.answer and "1" in final.answer

    streamed = "".join(d.data.text for d in deltas)
    # DATA ran on the page's text before it left (I4): the mobile number never streamed.
    assert "0501234567" not in streamed and "REDACTED" in streamed
    record = await services.reports.version(principal.tenant_id, principal.user_id, done[0].data.id, 1)
    page = (await services.reports.page(record, 10)).decode()
    assert record.size == len(page.encode()) == done[0].data.size
    assert "cdn.example.com" not in page and "0501234567" not in page
    assert page.startswith('<!doctype html><html lang="ar" dir="rtl">')
    # The labels (ADR-0388): 3.6 and 2031 are in the message; 3.1 and 2030 are the model's own.
    general = 'data-kit-source="general" data-kit-label="من المعرفة العامة" title="من المعرفة العامة">3.1</span>'
    assert general in page
    assert 'data-kit-source="message" data-kit-label="من: طلبك" title="من: طلبك">3.6</span>' in page
    assert "غير موثق" not in page
    data = json.loads(re.search(r'id="report-data">(.*?)</script>', page).group(1))
    assert data["charts"]["g"]["datasets"][0]["general"] == [0]
    assert data["charts"]["g"]["datasets"][0]["sources"] == ["من المعرفة العامة", "من: طلبك"]
    assert data["charts"]["g"]["label_sources"] == ["من المعرفة العامة", "من: طلبك"]
    assert '<section class="kit-sources" data-kit="sources"' in page and "<h2>المصادر</h2>" in page
    assert record.figures >= 6 and record.general == 3

    # D4: a follow-up with no picker and no keyword is a candidate because the conversation holds a report.
    events2, _, admission2 = await _turn(services, principal, "غيّر الألوان إلى الأخضر", conversation=conversation)
    assert admission2.profile == "report"
    assert events2[-1].type == "done", events2[-1]
    done2 = _of(events2, "artifact_done")[0].data
    assert done2.id == done[0].data.id and done2.version == 2
    assert "report_current" in gateway.blocks["report_writer"][1]
    report = await services.reports.get(principal.tenant_id, principal.user_id, done2.id)
    assert [v.version for v in report.versions] == [1, 2] and report.title == "لوحة النمو"
    page2 = (await services.reports.page(await services.reports.version(
        principal.tenant_id, principal.user_id, done2.id, 2), 10)).decode()
    assert "--kit-accent:#0f766e" in page2
    # 3.1 stays general knowledge in v2; 3.6 keeps the label v1 gave it, though v2's message has no figure.
    assert general in page2
    assert 'data-kit-source="message" data-kit-label="من: طلبك" title="من: طلبك">3.6</span>' in page2


async def test_only_the_owner_reads_a_report_through_the_agent_api(reports):
    from app.main import create_app

    services, _ = reports({"report_writer": PAGE})
    owner = _principal()
    events, conversation, _ = await _turn(services, owner, "تقرير تفاعلي عن النمو", output="report")
    report_id = _of(events, "artifact_done")[0].data.id
    app = create_app(start_background=False, services=services)
    headers = {"X-Internal-Token": services.settings.agent_internal_token}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as http:
        mine = {**headers, "X-User-Id": owner.user_id, "X-Tenant-Id": owner.tenant_id}
        other = {**headers, "X-User-Id": "someone-else", "X-Tenant-Id": owner.tenant_id}
        assert (await http.get(f"/v1/reports/{report_id}", headers=mine)).status_code == 200
        listed = await http.get(f"/v1/conversations/{conversation}/reports", headers=mine)
        assert [r["id"] for r in listed.json()["reports"]] == [report_id]
        for path in (f"/v1/reports/{report_id}", f"/v1/reports/{report_id}/versions/1",
                     f"/v1/reports/{report_id}/versions/1/page?form=download"):
            refused = await http.get(path, headers=other)
            assert refused.status_code == 404 and refused.json()["error"]["code"] == "report_not_found"
        assert (await http.get(f"/v1/conversations/{conversation}/reports", headers=other)).json() == {"reports": []}
        download = await http.get(f"/v1/reports/{report_id}/versions/1/page?form=download", headers=mine)
        assert download.status_code == 200 and "data:font/woff2;base64," in download.text
        assert not re.search(r"""(?:src|href)\s*=\s*["']?(?:https?:)?//""", download.text)
        view = await http.get(
            f"/v1/reports/{report_id}/versions/1/page",
            params={"form": "view", "kit_base": "http://localhost:8080/report-kit/v1/"}, headers=mine,
        )
        assert '<script src="http://localhost:8080/report-kit/v1/kit.js" defer></script>' in view.text
        bad_base = await http.get(
            f"/v1/reports/{report_id}/versions/1/page",
            params={"form": "view", "kit_base": "https://evil.example.com/x/"}, headers=mine,
        )
        assert bad_base.status_code in (400, 422)


async def test_a_keyword_makes_a_candidate_and_the_router_decides(reports):
    principal = _principal()
    services, gateway = reports({"report_writer": PAGE, "router": ROUTE_REPORT})
    events, _, admission = await _turn(services, principal, "أبي داشبورد عن سوق العمل")
    assert admission.profile == "report" and _of(events, "artifact_done")
    services, gateway = reports({"router": ROUTE_TEXT})
    events, _, admission = await _turn(services, principal, "ما معنى كلمة عرض في الاقتصاد؟")
    assert admission.profile == "report"  # «عرض» is a keyword: a candidate, and the router said text
    assert events[-1].type == "done" and not _of(events, "artifact_start")
    assert events[-1].data.state_digest.output == "text"
    assert "report_writer" not in gateway.calls


async def test_a_turn_with_no_keyword_no_report_and_no_picker_keeps_the_default_deadline(services):
    principal = _principal()
    runner = TurnRunner(services)
    request = TurnRequest(conversation_id=str(uuid.uuid4()), principal=principal, message="ما عاصمة اليابان؟")
    admission = await runner.admit(request)
    try:
        assert admission.profile is None and admission.deadline_s == 120
    finally:
        await admission.release()
    picked_text = TurnRequest(
        conversation_id=str(uuid.uuid4()), principal=principal, message="داشبورد", output="text"
    )
    admission = await runner.admit(picked_text)
    try:
        assert admission.profile is None  # the picker said text: no candidate, the default deadline
    finally:
        await admission.release()


# report_b276fb00 v2 (owner decision D): Chinese letters inside Arabic text.
CJK = PAGE.replace("<h1>لوحة النمو</h1>", "<h1>لوحة النمو مع أندريه في拉斯</h1>")


async def test_a_page_outside_its_scripts_is_written_again_once_with_the_failure_named(reports):
    services, gateway = reports({"report_writer": [CJK, PAGE], "router": ROUTE_REPORT})
    principal = _principal()
    events, _, _ = await _turn(services, principal, "اعمل لي لوحة مؤشرات عن النمو", output="report")
    assert events[-1].type == "done", events[-1]
    assert len(_of(events, "artifact_start")) == 2  # the panel starts over on the second page
    assert len(gateway.systems["report_writer"]) == 2
    first, retry = gateway.systems["report_writer"]
    assert "رفضت المنصة محاولتك السابقة" not in first
    assert "رفضت المنصة محاولتك السابقة" in retry and "«في拉斯»" in retry
    done = _of(events, "artifact_done")[0].data
    record = await services.reports.version(principal.tenant_id, principal.user_id, done.id, 1)
    page = (await services.reports.page(record, 10)).decode()
    assert "拉" not in page and done.version == 1


async def test_a_page_that_fails_its_scripts_twice_ends_the_turn_with_the_validation_message(reports):
    data_cjk = PAGE.replace('"label":"النمو"', '"label":"النمو 增长"')
    services, gateway = reports({"report_writer": [CJK, data_cjk], "router": ROUTE_REPORT})
    principal = _principal()
    events, conversation, _ = await _turn(services, principal, "اعمل لي لوحة مؤشرات عن النمو", output="report")
    assert events[-1].type == "error" and events[-1].data.code == "validation_failed"
    assert not _of(events, "artifact_done") and len(gateway.systems["report_writer"]) == 2
    assert not await services.reports.has_report(principal.tenant_id, principal.user_id, conversation)


@pytest.mark.parametrize("show", [True, False])
async def test_the_model_strip_follows_the_setting_in_the_page_the_version_and_the_download(reports, show):
    # Owner decision C (ADR-0391): shown with the writer's call when the setting is true, never when false.
    from app.main import create_app

    services, _ = reports({"report_writer": PAGE})
    services = dataclasses.replace(services, artifacts=dataclasses.replace(services.artifacts, show_model_info=show))
    owner = _principal()
    events, _, _ = await _turn(services, owner, "تقرير تفاعلي عن النمو", output="report")
    report_id = _of(events, "artifact_done")[0].data.id
    record = await services.reports.version(owner.tenant_id, owner.user_id, report_id, 1)
    stored = (await services.reports.page(record, 10)).decode()
    assert ('data-kit="model-info"' in stored) is show
    if show:
        strip = re.search(r'<footer class="kit-model-info".*?</footer>', stored, re.S).group(0)
        tier = _of(events, "tier")[-1].data.tier  # the writer's tier, as the test stack resolves it
        assert "كتابة التقرير" in strip and f"<bdi>{tier}</bdi>" in strip and " ث</bdi>" in strip
        assert strip in stored[stored.index('data-kit="sources"'):]  # last, after the sources section
    # A page stored while it was shown never leaves once the setting is false.
    for setting in (show, False):
        app = create_app(start_background=False, services=dataclasses.replace(
            services, artifacts=dataclasses.replace(services.artifacts, show_model_info=setting)))
        mine = {"X-Internal-Token": services.settings.agent_internal_token, "X-User-Id": owner.user_id,
                "X-Tenant-Id": owner.tenant_id}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as http:
            download = (await http.get(f"/v1/reports/{report_id}/versions/1/page?form=download", headers=mine)).text
            version = (await http.get(f"/v1/reports/{report_id}/versions/1", headers=mine)).json()["html"]
        assert ('data-kit="model-info"' in download) is (show and setting)
        assert ('data-kit="model-info"' in version) is (show and setting)
