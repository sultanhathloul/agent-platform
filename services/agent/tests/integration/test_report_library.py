"""The report library, in-conversation awareness and long-conversation recall (report library PR, owner
decisions 1 to 5; ADR-0430 to ADR-0434), through real turns: Context Assembly, the router, the broker and
LiteLLM, with the models' texts replaced after the real calls (ADR-0075).

Negative tests: no tool, endpoint or block returns another user's report or conversation."""

import dataclasses
import json
import uuid

import httpx
import pytest

from app.api.runner import TurnRunner
from app.api.schemas import TurnRequest
from app.identity import Principal
from tests.fixtures.http import TOKEN, identity
from tests.fixtures.replace import ReplacingGateway

pytestmark = pytest.mark.integration

SPEC = {
    "title": "لوحة سوق العمل التجريبية",
    "subtitle": "الربع الثاني",
    "language": "ar",
    "sections": [
        {"kind": "kpi_row", "items": [{"label": "معدل البطالة", "value": 6.5, "unit": "%"}], "sources": ["general"]},
        {"kind": "table", "title": "أساطير الكرة", "columns": ["اللاعب", "البلد"],
         "rows": [["ميسي", "الأرجنتين"], ["بيليه", "البرازيل"]], "sources": ["general"]},
    ],
}
PAGE = json.dumps(SPEC, ensure_ascii=False)
EDITED = json.dumps({**SPEC, "sections": [*SPEC["sections"], {"kind": "text", "markdown": "إضافة."}]}, ensure_ascii=False)
ROUTE_RECALL = json.dumps({"capabilities": ["recall"], "skill": None, "output": "text", "edit": False,
                           "complexity": "low", "risk": "low"})
ROUTE_EDIT = json.dumps({"capabilities": ["general"], "skill": None, "output": "report", "edit": True,
                         "complexity": "low", "risk": "low"})
PERMISSIONS = ("cap:general", "cap:recall")


class Capturing(ReplacingGateway):
    """Also keeps every call's package, by role."""

    def __init__(self, inner, replies):
        super().__init__(inner, replies)
        self.packages: dict[str, list] = {}

    async def call(self, decision, package, *, on_token=None, temperature=None):
        self.packages.setdefault(decision.role, []).append(package)
        return await super().call(decision, package, on_token=on_token, temperature=temperature)


def _principal(permissions=PERMISSIONS) -> Principal:
    return Principal(user_id=f"u-lib-{uuid.uuid4().hex[:6]}", permissions=permissions)


@pytest.fixture
def library(services):
    def make(replies):
        gateway = Capturing(services.gateway, replies)
        return dataclasses.replace(services, gateway=gateway), gateway

    return make


async def _turn(services, principal, message, *, conversation=None, output=None):
    request = TurnRequest(conversation_id=conversation or str(uuid.uuid4()), principal=principal, message=message,
                          locale="ar", output=output)
    runner = TurnRunner(services)
    admission = await runner.admit(request)
    events = [e async for e in runner.stream(request, f"req-{uuid.uuid4().hex[:6]}", admission=admission)]
    assert events[-1].type == "done", events[-1]
    return events, request.conversation_id


def _block(package, name):
    return next((b for b in package.blocks if b.name == name), None)


async def _report(services, principal, conversation=None):
    events, conversation = await _turn(services, principal, "اعمل لي لوحة مؤشرات عن سوق العمل",
                                       conversation=conversation, output="report")
    return events[-1].data.artifact.id, conversation


async def test_a_version_is_indexed_with_it_and_only_its_owner_finds_it(library):
    services, _ = library({"report_writer": PAGE})
    owner, other = _principal(), _principal()
    report_id, conversation = await _report(services, owner)
    async with services.db.connection() as conn:
        cursor = await conn.execute(
            "SELECT title, source_kinds, text_norm FROM agent.report_library WHERE report_id = %s", (report_id,))
        row = await cursor.fetchone()
    assert row["title"] == SPEC["title"] and row["source_kinds"] == ["general"] and "بطاله" in row["text_norm"]
    [hit] = await services.reports.search(owner.tenant_id, owner.user_id, ["بطاله"], limit=5)
    assert hit.report_id == report_id and hit.conversation_id == conversation and hit.score > 0
    assert await services.reports.search(other.tenant_id, other.user_id, ["بطاله"], limit=5) == []
    assert await services.reports.search(other.tenant_id, other.user_id, [], limit=5) == []
    assert await services.reports.search("another-tenant", owner.user_id, ["بطاله"], limit=5) == []


async def test_the_library_endpoint_lists_the_callers_own_reports_only(library):
    from app.main import create_app

    services, _ = library({"report_writer": PAGE})
    owner, other = _principal(), _principal()
    report_id, _ = await _report(services, owner)
    app = create_app(start_background=False, services=services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as http:
        mine = (await http.get("/v1/reports", params={"q": "البطالة"}, headers=identity(owner.user_id))).json()
        theirs = (await http.get("/v1/reports", params={"q": "البطالة"}, headers=identity(other.user_id))).json()
        listed = (await http.get("/v1/reports", params={"source": "general"}, headers=identity(owner.user_id))).json()
        kb_only = (await http.get("/v1/reports", params={"source": "kb:stats-gov"}, headers=identity(owner.user_id))).json()
        bad = await http.get("/v1/reports", params={"source": "kb:../x"}, headers=identity(owner.user_id))
        no_token = await http.get("/v1/reports", headers=identity(owner.user_id, token=None))
    assert [r["id"] for r in mine["reports"]] == [report_id]
    assert mine["reports"][0]["sections"] == ["أساطير الكرة"] and mine["reports"][0]["source_kinds"] == ["general"]
    assert theirs["reports"] == []
    assert report_id in [r["id"] for r in listed["reports"]] and kb_only["reports"] == []
    assert bad.status_code == 400 and no_token.status_code == 401


async def test_a_later_turn_lists_the_conversations_reports_and_the_window_carries_the_marker(library):
    services, gateway = library({"report_writer": PAGE})
    principal = _principal(("cap:general",))
    _, conversation = await _report(services, principal)
    await _turn(services, principal, "ما عاصمة المملكة؟", conversation=conversation, output="text")
    answer = gateway.packages["reasoning"][-1]
    index = _block(answer, "reports")
    assert index is not None and index.role == "user"
    assert "«لوحة سوق العمل التجريبية» (الإصدار 1)" in index.content and "معدل البطالة: 6.5%" in index.content
    marker = [b for b in answer.blocks if b.name == "window" and b.role == "assistant"][-1]
    assert "[تقرير: «لوحة سوق العمل التجريبية»، الإصدار 1." in marker.content
    assert "<!doctype" not in marker.content.lower()


async def test_a_question_about_a_report_reads_it_through_the_broker_and_answers_in_text(library):
    services, gateway = library({"report_writer": PAGE, "router": ROUTE_RECALL})
    principal = _principal()
    report_id, conversation = await _report(services, principal)
    events, _ = await _turn(services, principal, "منهم أساطير كرة القدم المذكورين في التقرير السابق؟",
                            conversation=conversation)
    final = events[-1].data
    assert final.state_digest.output == "text" and final.artifact is None
    tools = [e.data.tool for e in events if e.type == "tool_start"]
    assert tools == ["report_search", "report_search", "report_read", "conversation_recall"]
    findings = _block(gateway.packages["reasoning"][-1], "findings").content
    assert "| ميسي | الأرجنتين |" in findings and "من التقرير: لوحة سوق العمل التجريبية (الإصدار 1)" in findings
    async with services.db.connection() as conn:
        cursor = await conn.execute("SELECT latest_version FROM agent.reports WHERE id = %s", (report_id,))
        assert (await cursor.fetchone())["latest_version"] == 1  # a question is never an edit


async def test_no_tool_returns_another_users_report_or_conversation(library):
    services, gateway = library({"report_writer": PAGE, "router": ROUTE_RECALL})
    owner, other = _principal(), _principal()
    report_id, conversation = await _report(services, owner)
    await _turn(services, owner, "تذكر أن الرمز السري للمشروع هو زمرد-٤٢", conversation=conversation, output="text")

    # The other user asks, in their own conversation: nothing of the owner's is found or read.
    await _turn(services, other, "وش التقارير اللي سويتها عن سوق العمل؟ وش الرمز السري زمرد؟")
    findings = _block(gateway.packages["reasoning"][-1], "findings")
    text = findings.content if findings else ""
    assert "لوحة سوق العمل التجريبية" not in text and "ميسي" not in text and "زمرد-٤٢" not in text

    # Straight through the broker as the other user: the owner's id is report_not_found.
    broker = services.broker
    read = await broker.execute("report_read", {"report_id": report_id}, caller="recall", principal=other,
                                conversation_id=str(uuid.uuid4()))
    assert read.status == "error" and read.error == "report_not_found"
    searched = await broker.execute("report_search", {"query": "سوق العمل"}, caller="recall", principal=other,
                                    conversation_id=str(uuid.uuid4()))
    assert searched.status == "ok" and searched.data == []
    # A capability that does not declare `access` gets no library and no history (ADR-0431).
    assert broker.library_for("general", broker.scope_for("general", owner, conversation_id=conversation)) is None
    assert broker.history_for("general", [{"role": "user", "content": "x"}]) is None


async def test_conversation_recall_reads_only_the_turns_own_history(library):
    services, _ = library({})
    owner = _principal()
    history = [{"role": "user", "content": "جدولي:\nأ\tب\tج\n1\t2\t3\n4\t5\t6\n7\t8\t9"},
               {"role": "assistant", "content": "تم"}] + [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}] * 4
    found = await services.broker.execute("conversation_recall", {"query": "الجدول اللي أرسلته"}, caller="recall",
                                          principal=owner, conversation_id=str(uuid.uuid4()), history=history)
    # Verbatim but for whitespace: POST_TOOL's sanitizer folds tabs (README §10.3); the rows and cells stay.
    assert found.status == "ok" and "4 5 6" in found.data[0]["text"] and "7 8 9" in found.data[0]["text"]
    # No argument names a conversation: a call that tries fails validation, before anything runs.
    named = await services.broker.execute("conversation_recall", {"query": "x", "conversation_id": "c"},
                                          caller="recall", principal=owner, conversation_id="c", history=history)
    assert named.status == "error" and named.error == "invalid_arguments"


async def test_a_new_version_of_an_earlier_report_is_made_here_and_the_original_is_untouched(library):
    services, gateway = library({"report_writer": [PAGE, EDITED], "router": ROUTE_RECALL})
    principal = _principal()
    report_id, _ = await _report(services, principal)
    # Conversation B: a question about the earlier report reads it (and records the reference).
    _, second = await _turn(services, principal, "التقرير اللي سويته عن سوق العمل، وش نسبة البطالة فيه؟")
    findings = _block(gateway.packages["reasoning"][-1], "findings").content
    assert "معدل البطالة: 6.5%" in findings
    ref = await services.reports.referenced(principal.tenant_id, principal.user_id, second)
    assert ref is not None and ref.report_id == report_id

    gateway._replies["router"] = ROUTE_EDIT
    events, _ = await _turn(services, principal, "سوّ لي نسخة جديدة منه وأضف رسمًا للمشاركة في قوة العمل",
                            conversation=second)
    artifact = events[-1].data.artifact
    assert artifact is not None and artifact.id != report_id and artifact.version == 1
    assert "نسخةً جديدة من «لوحة سوق العمل التجريبية» (الإصدار 1)" in events[-1].data.answer
    writer = gateway.packages["report_writer"][-1]
    assert '"أساطير الكرة"' in _block(writer, "report_current").content  # the base spec was revised
    new = await services.reports.get(principal.tenant_id, principal.user_id, artifact.id)
    old = await services.reports.get(principal.tenant_id, principal.user_id, report_id)
    assert new.conversation_id == second and new.based_on == {"report_id": report_id, "version": 1}
    assert old.latest_version == 1 and len(old.versions) == 1


async def test_the_backfill_indexes_versions_written_before_the_library(library):
    services, _ = library({"report_writer": PAGE})
    principal = _principal()
    report_id, _ = await _report(services, principal)
    async with services.db.connection() as conn:
        await conn.execute("DELETE FROM agent.report_library WHERE report_id = %s", (report_id,))
    while await services.reports.backfill(50):
        pass
    [hit] = await services.reports.search(principal.tenant_id, principal.user_id, ["اساطير"], limit=5)
    assert hit.report_id == report_id
    assert await services.reports.backfill(50) == 0  # idempotent: nothing left


async def test_the_history_reply_is_the_newest_page_and_says_how_many_it_left_out(services):
    from app.main import create_app

    history = services.library.history.model_copy(update={"page_messages": 20})
    paged = dataclasses.replace(services, library=services.library.model_copy(update={"history": history}))
    principal = _principal(("cap:general",))
    conversation = str(uuid.uuid4())
    for i in range(12):
        await _turn(paged, principal, f"السؤال رقم {i}", conversation=conversation, output="text")
    app = create_app(start_background=False, services=paged)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as http:
        body = (await http.get(f"/v1/conversations/{conversation}/messages", headers=identity(principal.user_id))).json()
    assert body["omitted"] == 4 and len(body["messages"]) == 20
    assert body["messages"][0]["text"] == "السؤال رقم 2"  # the oldest kept is a question: pairs stay whole
