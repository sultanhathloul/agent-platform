"""My files (my-files PR, owner decisions 1 to 5; ADR-0442 to ADR-0446), through real turns: Context Assembly,
the router, the broker, the phase 04 index and LiteLLM, with the models' texts replaced after the real calls
(ADR-0075).

Negative tests: no path (the list, the tools, the evidence fetch, the delete) returns or touches another user's
file; an earlier file is never used unless the user's message refers to it."""

import dataclasses
import json
import uuid

import httpx
import pytest

from app.api.runner import TurnRunner
from app.api.schemas import TurnRequest
from app.identity import Principal
from app.rag.backend import DocumentScope
from app.rag.types import Owner
from tests.fixtures.backends import Harness
from tests.fixtures.http import identity
from tests.fixtures.replace import ReplacingGateway

pytestmark = pytest.mark.integration

LAB = [
    "\n".join(
        [
            "تقرير مختبر التحاليل الطبية",
            "اسم المريض: مريض تجريبي",
            "تحليل صورة الدم الكاملة",
            "الهيموجلوبين 13.2 غرام لكل ديسيلتر",
            "كريات الدم البيضاء 6.1 ألف لكل ميكرولتر",
        ]
    ),
    "\n".join(
        [
            "تحليل السكر التراكمي للدم",
            "السكر التراكمي 5.4 بالمئة",
            "النتيجة ضمن المعدل الطبيعي للبالغين",
            "ملاحظات الطبيب: يعاد التحليل بعد ستة أشهر",
            "تحليل وظائف الكلى ضمن المعدل الطبيعي",
        ]
    ),
]
ROUTE_FILES = json.dumps(
    {"capabilities": ["files"], "skill": None, "output": "text", "complexity": "low", "risk": "low"}
)
ROUTE_GENERAL = json.dumps(
    {"capabilities": ["general"], "skill": None, "output": "text", "complexity": "low", "risk": "low"}
)
PERMISSIONS = ("cap:general", "cap:docs", "cap:files")
ASKED = "رفعت لك تحليل الدم في محادثة ثانية، كم الهيموجلوبين؟"
CITED = "من ملفك: تحليل-الدم: الهيموجلوبين 13.2 غرام لكل ديسيلتر في تحليل صورة الدم الكاملة."


class Capturing(ReplacingGateway):
    """Also keeps every call's package, by role."""

    def __init__(self, inner, replies):
        super().__init__(inner, replies)
        self.packages: dict[str, list] = {}

    async def call(self, decision, package, *, on_token=None, temperature=None):
        self.packages.setdefault(decision.role, []).append(package)
        return await super().call(decision, package, on_token=on_token, temperature=temperature)


def _principal(permissions=PERMISSIONS) -> Principal:
    return Principal(user_id=f"u-files-{uuid.uuid4().hex[:6]}", permissions=permissions)


def _owner(principal: Principal) -> Owner:
    return Owner(tenant_id=principal.tenant_id, user_id=principal.user_id)


@pytest.fixture
def files(services):
    def make(replies):
        gateway = Capturing(services.gateway, replies)
        return dataclasses.replace(services, gateway=gateway), gateway

    return make


async def _turn(services, principal, message, *, conversation=None, attachments=(), locale="ar"):
    request = TurnRequest(
        conversation_id=conversation or str(uuid.uuid4()),
        principal=principal,
        message=message,
        attachment_ids=list(attachments),
        locale=locale,
    )
    runner = TurnRunner(services)
    admission = await runner.admit(request)
    events = [e async for e in runner.stream(request, f"req-{uuid.uuid4().hex[:6]}", admission=admission)]
    assert events[-1].type == "done", events[-1]
    return events, request.conversation_id


async def _uploaded(services, principal, name="تحليل-الدم.pdf", pages=LAB, message="هذا تحليل الدم الخاص بي"):
    """A file uploaded and indexed in a conversation of its own, which a first turn names."""
    conversation = str(uuid.uuid4())
    scope = DocumentScope.session(
        tenant_id=principal.tenant_id, user_id=principal.user_id, conversation_id=conversation
    )
    harness = Harness("internal", services)
    document = await harness.upload(scope, name, pages)
    assert await harness.ingest(document) == "indexed"
    await _turn(services, principal, message, conversation=conversation, attachments=[document])
    return document, conversation


def _block(package, name):
    return next((b for b in package.blocks if b.name == name), None)


def _retrieved(package) -> str:
    """Every retrieval block's text (one block per chunk)."""
    return "\n".join(b.content for b in package.blocks if b.name == "retrieval")


def _tools(events) -> list[str]:
    return [e.data.tool for e in events if e.type == "tool_start"]


async def test_a_file_of_another_conversation_is_searched_on_request_and_cited(files):
    services, gateway = files({"router": ROUTE_FILES, "reasoning": CITED})
    principal = _principal()
    document, first = await _uploaded(services, principal)
    events, second = await _turn(services, principal, ASKED)
    assert second != first
    assert _tools(events) == ["file_search"]
    answer = gateway.packages["reasoning"][-1]
    findings = _block(answer, "findings").content
    assert "«تحليل-الدم» رُفع في محادثة" in findings and "من ملفك:" in findings
    assert "الهيموجلوبين 13.2" in _retrieved(answer)
    citations = events[-1].data.citations
    assert [c.document_id for c in citations] == [document] and citations[0].title == "تحليل-الدم.pdf"


async def test_file_read_reads_the_named_pages_of_the_best_file(files):
    services, gateway = files({"router": ROUTE_FILES})
    principal = _principal()
    await _uploaded(services, principal)
    events, _ = await _turn(services, principal, "الملف اللي أرسلته لك قبل عن التحاليل، وش في الصفحة 2؟")
    assert _tools(events) == ["file_search", "file_read"]
    assert "السكر التراكمي 5.4" in _retrieved(gateway.packages["reasoning"][-1])


async def test_an_earlier_file_is_never_used_unless_the_message_refers_to_one(files):
    principal = _principal()
    # No upload or earlier-file word: `files` is no candidate, so no router call adds it and no tool runs.
    services, gateway = files({})
    await _uploaded(services, principal)
    events, _ = await _turn(services, principal, "كم المعدل الطبيعي للهيموجلوبين عند البالغين؟")
    assert "file_search" not in _tools(events) and "file_read" not in _tools(events)
    assert _block(gateway.packages["reasoning"][-1], "retrieval") is None
    # Even when the router chooses `files`, a message that refers to no earlier file runs no tool.
    services, gateway = files({"router": ROUTE_FILES})
    events, _ = await _turn(services, principal, "اشرح لي ما هو الهيموجلوبين في محادثة ثانية")
    assert _tools(events) == []
    english, _ = await _turn(services, principal, "Please present the hemoglobin ranges")
    assert _tools(english) == []
    # An upload word alone is no reference to a file (ADR-0449), whatever the router chose.
    for message in ("رفعت سعر المنتج، كيف أحسب الربح؟", "حملت التطبيق أمس"):
        events, _ = await _turn(services, principal, message)
        assert _tools(events) == [], message
    events, _ = await _turn(services, principal, "رفعت لك ملف الفحوصات في محادثة ثانية")
    assert _tools(events) == ["file_search"]


async def test_no_path_returns_another_users_file(files):
    services, gateway = files({"router": ROUTE_FILES})
    owner, other = _principal(), _principal()
    document, conversation = await _uploaded(services, owner)

    # The other user asks the same question: nothing of the owner's is found, retrieved or cited.
    events, _ = await _turn(services, other, ASKED)
    answer = gateway.packages["reasoning"][-1]
    assert _block(answer, "retrieval") is None and "تحليل-الدم" not in _block(answer, "findings").content
    assert events[-1].data.citations == []

    broker = services.broker
    read = await broker.execute(
        "file_read", {"file_id": document}, caller="files", principal=other, conversation_id=str(uuid.uuid4())
    )
    assert read.status == "error" and read.error == "file_not_found"
    searched = await broker.execute(
        "file_search", {"query": "الهيموجلوبين"}, caller="files", principal=other, conversation_id=str(uuid.uuid4())
    )
    assert searched.status == "ok" and searched.sources == [] and searched.data == []
    # No argument names an owner or a conversation: a call that tries fails validation.
    named = await broker.execute(
        "file_search", {"query": "x", "user_id": owner.user_id}, caller="files", principal=other, conversation_id="c"
    )
    assert named.status == "error" and named.error == "invalid_arguments"
    # The evidence step may fetch only from the caller's own files' collections.
    assert f"sess_{conversation}" in await broker.owned_collections("files", owner)
    assert f"sess_{conversation}" not in await broker.owned_collections("files", other)
    # A capability that does not declare `user_files` gets no library (ADR-0443).
    assert broker.files_for("docs", broker.scope_for("docs", owner, conversation_id=conversation)) is None
    assert await broker.owned_collections("docs", owner) == ()
    # The other user's list holds none of it.
    entries, _ = await services.files.page(_owner(other))
    assert entries == []


async def test_files_is_guarded_as_docs_and_a_file_turn_writes_no_memory(files, monkeypatch):
    extracted = json.dumps(
        [{"kind": "fact_about_user", "text": "المستخدم يعمل ممرضًا.", "confidence": 0.9}], ensure_ascii=False
    )
    services, _ = files({"router": ROUTE_FILES, "extractor": extracted})
    principal = _principal()
    await _uploaded(services, principal)
    seen: list[tuple] = []
    evaluate = services.guard.evaluate

    async def spying(checkpoint, payload, *, principal, context):
        seen.append((str(checkpoint), tuple(context.tools)))
        return await evaluate(checkpoint, payload, principal=principal, context=context)

    monkeypatch.setattr(services.guard, "evaluate", spying)
    await _turn(services, principal, "أنا أعمل ممرضًا، " + ASKED)
    # POST_TOOL on the file's passages and DATA on the answer carry the docs scope beside files (decision 4).
    assert any("POST_TOOL" in c and "files" in t and "docs" in t for c, t in seen)
    assert any("DATA" in c and "files" in t and "docs" in t for c, t in seen)
    async with services.db.connection() as conn:
        cursor = await conn.execute(
            "SELECT count(*) AS n FROM agent.memory_items WHERE tenant_id = %s AND user_id = %s",
            (principal.tenant_id, principal.user_id),
        )
        assert (await cursor.fetchone())["n"] == 0


async def test_the_page_lists_the_callers_files_and_a_delete_removes_them_everywhere(files):
    from app.main import create_app

    services, _ = files({"router": ROUTE_FILES, "reasoning": CITED})
    owner, other = _principal(), _principal()
    document, first = await _uploaded(services, owner)
    second_doc, _ = await _uploaded(
        services, owner, name="عقد-الإيجار.pdf", pages=["عقد إيجار شقة سكنية لمدة سنة"], message="هذا عقد الشقة"
    )
    events, asked_in = await _turn(services, owner, ASKED)
    assert [c.document_id for c in events[-1].data.citations] == [document]
    app = create_app(start_background=False, services=services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as http:
        listed = (await http.get("/v1/files", headers=identity(owner.user_id))).json()
        searched = (await http.get("/v1/files", params={"q": "عقد"}, headers=identity(owner.user_id))).json()
        by_title = (await http.get("/v1/files", params={"q": "تحليل الدم الخاص"}, headers=identity(owner.user_id))).json()
        typed = (await http.get("/v1/files", params={"kind": "docx"}, headers=identity(owner.user_id))).json()
        theirs = (await http.get("/v1/files", headers=identity(other.user_id))).json()
        paged = (await http.get("/v1/files", params={"limit": 1}, headers=identity(owner.user_id))).json()
        bad = await http.get("/v1/files", params={"kind": "exe"}, headers=identity(owner.user_id))
        no_token = await http.get("/v1/files", headers=identity(owner.user_id, token=None))

        assert [f["name"] for f in listed["files"]] == ["عقد-الإيجار.pdf", "تحليل-الدم.pdf"]
        row = listed["files"][1]
        assert row["conversation_id"] == first and row["conversation_title"] == "هذا تحليل الدم الخاص بي"
        assert row["kind"] == "pdf" and row["status"] == "indexed" and "object_key" not in row
        assert [f["name"] for f in searched["files"]] == ["عقد-الإيجار.pdf"]
        assert {f["name"] for f in by_title["files"]} == {"تحليل-الدم.pdf"}
        assert typed["files"] == [] and theirs["files"] == []
        assert len(paged["files"]) == 1 and paged["next_before"] is not None
        assert bad.status_code == 400 and no_token.status_code == 401

        # Another user cannot delete it: their own conversation id is 404, the owner's conversation 403.
        elsewhere = await http.delete(
            f"/v1/conversations/{uuid.uuid4()}/attachments/{document}", headers=identity(other.user_id)
        )
        forbidden = await http.delete(
            f"/v1/conversations/{first}/attachments/{document}", headers=identity(other.user_id)
        )
        assert elsewhere.status_code == 404 and forbidden.status_code == 403
        assert await services.files.get(_owner(owner), document) is not None

        deleted = await http.delete(f"/v1/conversations/{first}/attachments/{document}", headers=identity(owner.user_id))
        again = await http.delete(f"/v1/conversations/{first}/attachments/{document}", headers=identity(owner.user_id))
        assert deleted.status_code == 204 and again.status_code == 404
        history_first = (await http.get(f"/v1/conversations/{first}/messages", headers=identity(owner.user_id))).json()
        history_asked = (
            await http.get(f"/v1/conversations/{asked_in}/messages", headers=identity(owner.user_id))
        ).json()
    # Gone from the index, the context packages, the store and every conversation's history.
    async with services.db.connection() as conn:
        cursor = await conn.execute("SELECT count(*) AS n FROM agent.rag_chunks WHERE attachment_id = %s", (document,))
        assert (await cursor.fetchone())["n"] == 0
        cursor = await conn.execute(
            "SELECT count(*) AS n FROM agent.context_packages WHERE attachment_ids @> ARRAY[%s]::uuid[]", (document,)
        )
        assert (await cursor.fetchone())["n"] == 0
    assert all(m["attachments"] == [] for m in history_first["messages"])
    assert all(c["document_id"] != document for m in history_asked["messages"] for c in m["citations"])
    assert [e.record.id for e in (await services.files.page(_owner(owner)))[0]] == [second_doc]
    # A later turn no longer finds it.
    services2, gateway = files({"router": ROUTE_FILES})
    await _turn(services2, owner, ASKED)
    assert "الهيموجلوبين 13.2" not in _retrieved(gateway.packages["reasoning"][-1])
