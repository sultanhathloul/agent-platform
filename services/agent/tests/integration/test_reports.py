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

def _spec(title: str = "لوحة النمو", **extra) -> dict:
    return {
        "title": title, "language": "ar", "period": "2031",
        "sections": [
            {"kind": "kpi_row", "items": [{"label": "النمو", "value": 3.6, "unit": "%"},
                                          {"label": "العام السابق", "value": 3.1, "unit": "%"}], "sources": ["general"]},
            {"kind": "text", "markdown": "للتواصل: 0501234567"},
            {"kind": "chart", "type": "line", "title": "النمو", "labels": ["2030", "2031"],
             "series": [{"name": "النمو", "data": [3.1, 3.6]}], "unit": "%", "sources": ["general"]},
        ],
        **extra,
    }


# The writer's answer is a report spec (option C, ADR-0406), in a fence as a model may write it.
PAGE = "```json\n" + json.dumps(_spec(), ensure_ascii=False) + "\n```"
EDITED = json.dumps({**_spec(), "sections": [*_spec()["sections"], {"kind": "callout", "tone": "success",
                                                                     "markdown": "النمو باللون الأخضر"}]}, ensure_ascii=False)
ROUTE_REPORT = '{"capabilities": ["general"], "skill": null, "output": "report", "complexity": "low", "risk": "low"}'
ROUTE_TEXT = '{"capabilities": ["general"], "skill": null, "output": "text", "complexity": "low", "risk": "low"}'
# With a report in the conversation the router also says whether the request edits it (ADR-0400).
ROUTE_EDIT = '{"capabilities": ["general"], "skill": null, "output": "report", "edit": true, "complexity": "low", "risk": "low"}'
ROUTE_NEW = '{"capabilities": ["general"], "skill": null, "output": "report", "edit": false, "complexity": "low", "risk": "low"}'


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
    services, gateway = reports({"report_writer": [PAGE, EDITED], "router": ROUTE_EDIT})
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
    # Option C (ADR-0406 to ADR-0408): the platform renders the spec; no label beside any figure; a report
    # with no KB, file or findings opens with the general-knowledge line and has no sources section.
    assert "kit-figure" not in page and "data-kit-label" not in page and "من: طلبك" not in page
    assert "هذا التقرير من المعرفة العامة للنموذج، دون مصادر موثقة" in page and 'data-kit="sources"' not in page
    assert '<div class="kit-kpi-value">3.6<span class="kit-kpi-unit">%</span></div>' in page
    chart = json.loads(re.search(r'class="kit-chart-data" id="c1">(.*?)</script>', page).group(1))
    assert chart["series"][0]["data"] == [3.1, 3.6] and chart["labels"] == ["2030", "2031"]
    stored = json.loads(re.search(r'class="kit-spec" id="report-spec">(.*?)</script>', page).group(1))
    assert record.spec == stored and all(s.get("sources", ["general"]) == ["general"] for s in stored["sections"])
    assert record.figures == 4 and record.general == record.figures

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
    # The edit changed the spec: v1's spec was its context, and v2 adds a section (ADR-0406).
    assert "النمو باللون الأخضر" in page2 and "هذا التقرير من المعرفة العامة" in page2


async def test_a_follow_up_on_another_subject_is_a_new_report_that_inherits_nothing(reports):
    """ADR-0400: the router's call decides edit or new; a new subject is version 1 of a new report, with no
    current version in its context and nothing of the first report's labels or sources."""
    services, gateway = reports({"report_writer": [PAGE, PAGE.replace("لوحة النمو", "لوحة الهلال")], "router": ROUTE_NEW})
    principal = _principal()
    events, conversation, _ = await _turn(services, principal, "اعمل لي لوحة مؤشرات عن النمو", output="report")
    first = _of(events, "artifact_done")[0].data
    events2, _, admission2 = await _turn(services, principal, "سوّ لي لوحة معلومات عن نادي الهلال", conversation=conversation)
    assert admission2.profile == "report" and events2[-1].type == "done", events2[-1]
    second = _of(events2, "artifact_done")[0].data
    assert second.id != first.id and second.version == 1
    assert "report_current" not in gateway.blocks["report_writer"][1]
    assert events2[-1].data.state_digest.output == "report"
    record = await services.reports.version(principal.tenant_id, principal.user_id, second.id, 1)
    assert record.citations == [] and record.general == record.figures


async def test_the_picker_asks_the_router_only_whether_it_is_an_edit(reports):
    services, gateway = reports({"report_writer": [PAGE, EDITED], "router": ROUTE_EDIT})
    principal = _principal()
    events, conversation, _ = await _turn(services, principal, "اعمل لي لوحة مؤشرات عن النمو", output="report")
    first = _of(events, "artifact_done")[0].data
    events2, _, _ = await _turn(services, principal, "غيّر الألوان", conversation=conversation, output="report")
    second = _of(events2, "artifact_done")[0].data
    assert second.id == first.id and second.version == 2
    assert "report_current" in gateway.blocks["report_writer"][1]


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
    events, _, admission = await _turn(services, principal, "ما معنى كلمة داشبورد؟")
    assert admission.profile == "report"  # «داشبورد» is a keyword: a candidate, and the router said text
    assert events[-1].type == "done" and not _of(events, "artifact_start")
    assert events[-1].data.state_digest.output == "text"
    assert "report_writer" not in gateway.calls
    # Owner decision 3 on PR #17 (ADR-0412): «عرض» alone is no longer a keyword.
    events, _, admission = await _turn(services, principal, "ما معنى كلمة عرض في الاقتصاد؟ اعرض لي مثالًا")
    assert admission.profile is None and events[-1].type == "done"


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
CJK = json.dumps(_spec("لوحة النمو مع أندريه في拉斯"), ensure_ascii=False)


async def test_a_page_outside_its_scripts_is_written_again_once_with_the_failure_named(reports):
    services, gateway = reports({"report_writer": [CJK, PAGE], "router": ROUTE_REPORT})
    principal = _principal()
    events, _, _ = await _turn(services, principal, "اعمل لي لوحة مؤشرات عن النمو", output="report")
    assert events[-1].type == "done", events[-1]
    assert len(_of(events, "artifact_start")) == 2  # the panel starts over on the second page
    assert len(gateway.systems["report_writer"]) == 2
    first, retry = gateway.systems["report_writer"]
    assert "رفضت المنصة إجابتك السابقة" not in first
    assert "رفضت المنصة إجابتك السابقة" in retry and "«في拉斯»" in retry
    done = _of(events, "artifact_done")[0].data
    record = await services.reports.version(principal.tenant_id, principal.user_id, done.id, 1)
    page = (await services.reports.page(record, 10)).decode()
    assert "拉" not in page and done.version == 1


async def test_a_page_that_fails_its_scripts_twice_ends_the_turn_with_the_validation_message(reports):
    data_cjk = PAGE.replace('"name": "النمو"', '"name": "النمو 增长"')
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
        assert stored.rindex('class="kit-section"') < stored.index(strip)  # last, after every section
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


async def test_a_reply_that_is_not_a_spec_is_written_again_once_then_ends_the_turn(reports):
    """The item 6 runs: the Al Hilal report came back as the chat line. A reply that is not a report spec is
    written again once with the failure named (ADR-0406), then ends validation_failed."""
    line = "أُنشئ التقرير «لوحة نادي الهلال» (الإصدار 1)."
    services, gateway = reports({"report_writer": [line, PAGE], "router": ROUTE_REPORT})
    events, _, _ = await _turn(services, _principal(), "اعمل لي لوحة مؤشرات عن النمو", output="report")
    assert events[-1].type == "done", events[-1]
    assert _of(events, "artifact_done")[0].data.size > 2000
    assert "لم تكن مواصفة تقرير صحيحة" in gateway.systems["report_writer"][1]
    assert "لم تكن مواصفة تقرير صحيحة" not in gateway.systems["report_writer"][0]

    services, gateway = reports({"report_writer": [line, line], "router": ROUTE_REPORT})
    events, _, _ = await _turn(services, _principal(), "اعمل لي لوحة مؤشرات عن النمو", output="report")
    assert events[-1].type == "error" and events[-1].data.code == "validation_failed"
    assert not _of(events, "artifact_done")


async def test_sections_stream_into_the_panel_as_each_one_completes(reports):
    services, _ = reports({"report_writer": PAGE, "router": ROUTE_REPORT})
    events, _, _ = await _turn(services, _principal(), "اعمل لي لوحة مؤشرات عن النمو", output="report")
    deltas = [d.data.text for d in _of(events, "artifact_delta")]
    assert len(deltas) == 3  # one per section, the first with the frame and the header
    assert deltas[0].startswith("<!doctype html>") and "<h1>لوحة النمو</h1>" in deltas[0] and 'class="kit-kpis kit-kpis-2"' in deltas[0]
    assert "0501234567" not in "".join(deltas) and 'data-chart="c1"' in deltas[2]
