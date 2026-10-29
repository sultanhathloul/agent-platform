"""Knowledge bases at the agent (phase 08, ADR-0333 to ADR-0339). Negative permission first (I7):
the admin endpoints refuse a call without the gateway's admin flag; another tenant's, a deleted and
an unknown KB are all `kb_not_found`; `GET /v1/capabilities` lists only the caller's tenant's live
KBs it holds kb:<slug>:read for; a turn selecting any other KB is refused `kb_forbidden` before
any stream; a deleted KB's chunks leave the index and nothing reaches it, by any path. Then the
contract: CRUD, documents with their D3 metadata and supersedes rules, cross-KB retrieval whose
citations carry the KB, title, publisher, date and page (I4), and the latest version of a source
first (D3)."""

import uuid
from datetime import date

import httpx
import pytest

from app.api.runner import TurnRunner
from app.api.schemas import TurnRequest
from app.identity import Principal
from app.kb.service import KbService
from app.kb.store import Actor
from tests.fixtures.documents import pdf_bytes
from tests.fixtures.http import TOKEN
from tests.fixtures.ingestion import ingestion
from tests.fixtures.replace import replacing

pytestmark = pytest.mark.integration

APRIL = ["آفاق الاقتصاد الإقليمي: يتوقع الصندوق نمو اقتصاد الواحة بنسبة 4.0 بالمئة في عام 2031."]
JULY = ["تحديث آفاق الاقتصاد الإقليمي: يتوقع الصندوق نمو اقتصاد الواحة بنسبة 4.5 بالمئة في عام 2031."]
LABOR = ["نظام العمل في الواحة: مدة إجازة الأمومة عشرة أسابيع مدفوعة الأجر بالكامل."]
TRADE = ["تقرير التجارة في الواحة: بلغت صادرات التمور 812 مليون ريال في عام 2030."]


def _slug() -> str:
    return f"t{uuid.uuid4().hex[:10]}"


def _admin(tenant: str = "default", *, admin: bool = True) -> dict[str, str]:
    headers = {"X-Internal-Token": TOKEN, "X-User-Id": "kb-admin", "X-Tenant-Id": tenant,
               "X-User-Name": "admin", "X-User-Display-Name": "%D9%85%D8%AF%D9%8A%D8%B1"}
    if admin:
        headers["X-User-Admin"] = "true"
    return headers


def _reader(*permissions: str, tenant: str = "default") -> Principal:
    return Principal(user_id=f"u-kb-{uuid.uuid4().hex[:8]}", tenant_id=tenant,
                     permissions=("cap:general", "cap:kb", *permissions))


@pytest.fixture
async def api(services, litellm):
    from app.main import create_app

    app = create_app(start_background=False, services=services)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as http:
        yield http


async def _create(api, slug: str, tenant: str = "default", name_ar: str = "قاعدة اختبار") -> dict:
    response = await api.post(
        "/v1/admin/kbs",
        json={"slug": slug, "name": {"ar": name_ar, "en": f"KB {slug}"},
              "description": {"ar": "وصف", "en": "description"}},
        headers=_admin(tenant),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _document(services, api, slug: str, pages, *, title: str, publisher: str = "صندوق الواحة",
                    published_on: str = "2031-04-15", supersedes: str | None = None,
                    tenant: str = "default", ingest: bool = True) -> dict:
    data = pdf_bytes([page.split("\n") for page in pages], visual=True)
    key = f"kb/{slug}/{uuid.uuid4()}"
    await services.attachments.objects.put(key, data, "application/pdf")
    metadata = {"title": title, "publisher": publisher, "published_on": published_on,
                "source_url": "https://example.org/report"}
    if supersedes:
        metadata["supersedes"] = supersedes
    response = await api.post(
        f"/v1/admin/kbs/{slug}/documents",
        json={"name": f"{title}.pdf", "mime": "application/pdf", "size": len(data), "object_key": key,
              "metadata": metadata},
        headers=_admin(tenant),
    )
    assert response.status_code == 201, response.text
    view = response.json()
    if ingest:
        assert await ingestion(services).pipeline.run(f"kb:{view['id']}") == "indexed"
    return view


async def _turn(services, principal: Principal, message: str, selected, reply: str):
    swapped, _ = replacing(services, {"reasoning": reply})
    request = TurnRequest(conversation_id=str(uuid.uuid4()), principal=principal, message=message, locale="ar",
                          selected_kbs=list(selected))
    return [e.model_dump(mode="json") async for e in TurnRunner(swapped).stream(request, "req-kb")]


async def _search(services, principal: Principal, selected, query: str):
    return await services.broker.execute(
        "kb_search", {"query": query}, caller="kb", principal=principal,
        conversation_id=str(uuid.uuid4()), selected_kbs=tuple(selected),
    )


# ------------------------------------------------------------------ negative permission first (I7)


async def test_the_admin_endpoints_refuse_a_call_without_the_admin_flag_or_the_token(api):
    slug = _slug()
    calls = [("GET", "/v1/admin/kbs", None), ("POST", "/v1/admin/kbs",
             {"slug": slug, "name": {"ar": "س", "en": "s"}}), ("GET", f"/v1/admin/kbs/{slug}", None),
             ("DELETE", f"/v1/admin/kbs/{slug}", None), ("GET", f"/v1/admin/kbs/{slug}/documents", None)]
    for method, path, body in calls:
        refused = await api.request(method, path, json=body, headers=_admin(admin=False))
        assert refused.status_code == 403 and refused.json()["error"]["code"] == "forbidden", path
        anonymous = await api.request(method, path, json=body,
                                      headers={**_admin(), "X-Internal-Token": "wrong"})
        assert anonymous.status_code == 401, path
    assert (await api.get(f"/v1/admin/kbs/{slug}", headers=_admin())).status_code == 404  # nothing was created


async def test_another_tenants_a_deleted_and_an_unknown_kb_are_all_not_found(services, api):
    slug = _slug()
    await _create(api, slug)
    for tenant in ("t2",):
        assert (await api.get(f"/v1/admin/kbs/{slug}", headers=_admin(tenant))).status_code == 404
        assert slug not in [k["slug"] for k in (await api.get("/v1/admin/kbs", headers=_admin(tenant))).json()["kbs"]]
        assert (await api.get(f"/v1/admin/kbs/{slug}/documents", headers=_admin(tenant))).status_code == 404
        assert (await api.delete(f"/v1/admin/kbs/{slug}", headers=_admin(tenant))).status_code == 404
    deleted = await api.delete(f"/v1/admin/kbs/{slug}", headers=_admin())
    assert deleted.status_code == 200 and deleted.json() == {"slug": slug, "role": f"kb:{slug}:read",
                                                            "index_purged": True}
    for path in (f"/v1/admin/kbs/{slug}", f"/v1/admin/kbs/{slug}/documents", f"/v1/admin/kbs/{_slug()}"):
        response = await api.get(path, headers=_admin())
        assert response.status_code == 404 and response.json()["error"]["code"] == "kb_not_found", path


async def test_capabilities_list_only_the_callers_live_kbs_it_may_read(services, api):
    readable, deleted, unheld, foreign = _slug(), _slug(), _slug(), _slug()
    for slug in (readable, deleted, unheld):
        await _create(api, slug)
    await _create(api, foreign, tenant="t2")
    await api.delete(f"/v1/admin/kbs/{deleted}", headers=_admin())
    permissions = ",".join(["cap:general", "cap:kb", *(f"kb:{s}:read" for s in (readable, deleted, foreign))])
    response = await api.get("/v1/capabilities", headers={"X-Internal-Token": TOKEN, "X-User-Id": "u1",
                                                          "X-Tenant-Id": "default",
                                                          "X-User-Permissions": permissions})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [k["slug"] for k in body["knowledge_bases"] if k["slug"].startswith("t")] == [readable]
    assert {c["slug"] for c in body["capabilities"]} >= {"general", "kb"}
    assert {"slug", "name", "description", "icon"} <= set(body["knowledge_bases"][0])
    # Without cap:kb the capability is not listed; a malformed permission is refused.
    bare = await api.get("/v1/capabilities", headers={"X-Internal-Token": TOKEN, "X-User-Id": "u1",
                                                      "X-Tenant-Id": "default", "X-User-Permissions": "cap:general"})
    assert "kb" not in {c["slug"] for c in bare.json()["capabilities"]} and bare.json()["knowledge_bases"] == []
    malformed = await api.get("/v1/capabilities", headers={"X-Internal-Token": TOKEN, "X-User-Id": "u1",
                                                           "X-Tenant-Id": "default", "X-User-Permissions": "admin"})
    assert malformed.status_code == 400


async def test_a_turn_selecting_a_kb_the_caller_may_not_use_is_refused_before_any_stream(services, api):
    live, deleted, foreign = _slug(), _slug(), _slug()
    await _create(api, live)
    await _create(api, deleted)
    await _create(api, foreign, tenant="t2")
    await api.delete(f"/v1/admin/kbs/{deleted}", headers=_admin())
    holder = _reader(*(f"kb:{s}:read" for s in (live, deleted, foreign)))
    refused = [
        (_reader(), [live]),  # no role
        (holder, [deleted]),  # deleted, role held
        (holder, [foreign]),  # another tenant's, role held
        (holder, [_slug()]),  # unknown
        (holder, [live, deleted]),  # one of two
    ]
    for principal, selected in refused:
        body = TurnRequest(conversation_id=str(uuid.uuid4()), principal=principal, message="مرحبا",
                           selected_kbs=selected)
        response = await api.post("/v1/turns", content=body.model_dump_json(),
                                  headers={"X-Internal-Token": TOKEN, "Content-Type": "application/json"})
        assert response.status_code == 403, selected
        assert response.json()["error"]["code"] == "kb_forbidden", selected
    body = TurnRequest(conversation_id=str(uuid.uuid4()), principal=holder, message="مرحبا", selected_kbs=[live])
    allowed = await api.post("/v1/turns", content=body.model_dump_json(),
                             headers={"X-Internal-Token": TOKEN, "Content-Type": "application/json"})
    assert allowed.status_code == 200


async def test_a_deleted_kb_leaves_the_index_and_no_path_reaches_it(services, api):
    slug = _slug()
    await _create(api, slug)
    document = await _document(services, api, slug, LABOR, title="نظام العمل")
    reader = _reader(f"kb:{slug}:read")
    found = await _search(services, reader, [slug], "مدة إجازة الأمومة")
    assert [s["attachment_id"] for s in found.sources][:1] == [document["id"]]
    async with services.db.connection() as conn:
        before = await (await conn.execute(
            "SELECT count(*) AS n FROM agent.rag_chunks WHERE kb_document_id = %s", (document["id"],))).fetchone()
    assert before["n"] > 0
    await api.delete(f"/v1/admin/kbs/{slug}", headers=_admin())
    async with services.db.connection() as conn:
        after = await (await conn.execute(
            "SELECT count(*) AS n FROM agent.rag_chunks WHERE kb_document_id = %s", (document["id"],))).fetchone()
    assert after["n"] == 0
    # The broker's scope still names the collection for a holder who selected it: nothing comes back.
    gone = await _search(services, reader, [slug], "مدة إجازة الأمومة")
    assert gone.status == "ok" and gone.sources == []
    assert await services.kbs.catalog("default", [slug]) == {}
    assert await services.kbs.readable(reader) == []


async def test_a_kb_not_selected_or_not_held_is_never_searched(services, api):
    labor, trade = _slug(), _slug()
    await _create(api, labor)
    await _create(api, trade)
    await _document(services, api, labor, LABOR, title="نظام العمل")
    await _document(services, api, trade, TRADE, title="تقرير التجارة")
    both = _reader(f"kb:{labor}:read", f"kb:{trade}:read")
    only_labor = await _search(services, both, [labor], "صادرات التمور وإجازة الأمومة")
    assert {s["kb"] for s in only_labor.sources} == {labor}
    unheld = await _search(services, _reader(f"kb:{labor}:read"), [labor, trade], "صادرات التمور")
    assert {s["kb"] for s in unheld.sources} <= {labor}
    foreign = await _search(services, _reader(f"kb:{labor}:read", tenant="t2"), [labor], "إجازة الأمومة")
    assert foreign.sources == []


# ------------------------------------------------------------------ CRUD and documents


async def test_create_list_and_a_taken_slug(services, api):
    slug = _slug()
    created = await _create(api, slug, name_ar="الاقتصاد الدولي")
    assert created["role"] == f"kb:{slug}:read" and created["name"]["ar"] == "الاقتصاد الدولي"
    assert created["created_by_username"] == "admin" and created["created_by_name"] == "مدير"
    again = await api.post("/v1/admin/kbs", json={"slug": slug, "name": {"ar": "ب", "en": "b"}}, headers=_admin())
    assert again.status_code == 409 and again.json()["error"]["code"] == "kb_exists"
    listed = (await api.get("/v1/admin/kbs", headers=_admin())).json()["kbs"]
    assert slug in [k["slug"] for k in listed]
    await api.delete(f"/v1/admin/kbs/{slug}", headers=_admin())
    reused = await api.post("/v1/admin/kbs", json={"slug": slug, "name": {"ar": "ب", "en": "b"}}, headers=_admin())
    assert reused.status_code == 409  # a deleted KB's slug and role stay taken (I1)
    bad = await api.post("/v1/admin/kbs", json={"slug": "Bad Slug", "name": {"ar": "ب", "en": "b"}}, headers=_admin())
    assert bad.status_code == 400


async def test_documents_carry_their_metadata_and_supersedes_stays_a_chain_in_one_kb(services, api):
    slug, other = _slug(), _slug()
    await _create(api, slug)
    await _create(api, other)
    april = await _document(services, api, slug, APRIL, title="آفاق أبريل", published_on="2031-04-15")
    assert april["status"] == "pending" and april["metadata"]["publisher"] == "صندوق الواحة"
    july = await _document(services, api, slug, JULY, title="آفاق يوليو", published_on="2031-07-20",
                           supersedes=april["id"])
    elsewhere = await _document(services, api, other, TRADE, title="تقرير", ingest=False)
    listed = (await api.get(f"/v1/admin/kbs/{slug}/documents", headers=_admin())).json()["documents"]
    by_id = {d["id"]: d for d in listed}
    assert by_id[april["id"]]["status"] == "indexed" and by_id[april["id"]]["superseded_by"] == july["id"]
    assert by_id[july["id"]]["metadata"]["supersedes"] == april["id"]
    assert by_id[july["id"]]["metadata"]["published_on"] == "2031-07-20"

    def meta(**changes):
        return {"title": "آفاق", "publisher": "صندوق الواحة", "published_on": "2031-04-15", **changes}

    path = f"/v1/admin/kbs/{slug}/documents"
    for document, supersedes in [
        (april["id"], april["id"]),  # itself
        (april["id"], july["id"]),  # a loop
        (april["id"], elsewhere["id"]),  # another KB's document
        (april["id"], str(uuid.uuid4())),  # unknown
    ]:
        response = await api.put(f"{path}/{document}/metadata", json=meta(supersedes=supersedes), headers=_admin())
        assert response.status_code == 409 and response.json()["error"]["code"] == "kb_supersedes_invalid", supersedes
    third = await _document(services, api, slug, JULY, title="نسخة ثالثة", ingest=False)
    second = await api.put(f"{path}/{third['id']}/metadata", json=meta(supersedes=april["id"]), headers=_admin())
    assert second.status_code == 409  # July already supersedes April: a chain, never a fork
    fixed = await api.put(f"{path}/{april['id']}/metadata",
                          json=meta(title="آفاق الاقتصاد - أبريل", source_url="https://example.org/apr"),
                          headers=_admin())
    assert fixed.status_code == 200 and fixed.json()["metadata"]["title"] == "آفاق الاقتصاد - أبريل"
    assert (await api.put(f"{path}/{uuid.uuid4()}/metadata", json=meta(), headers=_admin())).status_code == 404
    wrong_type = await api.post(path, json={"name": "a.exe", "mime": "x", "size": 1, "object_key": "k",
                                            "metadata": meta()}, headers=_admin())
    assert wrong_type.status_code == 415


# ------------------------------------------------------------------ retrieval, citations, versions (I4, D3)


async def test_cross_kb_retrieval_cites_kb_title_publisher_date_and_page(services, api):
    labor, trade = _slug(), _slug()
    await _create(api, labor, name_ar="أنظمة العمل")
    await _create(api, trade, name_ar="التجارة")
    labor_doc = await _document(services, api, labor, LABOR, title="نظام العمل", publisher="وزارة الموارد",
                                published_on="2030-01-10")
    trade_doc = await _document(services, api, trade, TRADE, title="تقرير التجارة", publisher="هيئة الإحصاء",
                                published_on="2031-02-01")
    reader = _reader(f"kb:{labor}:read", f"kb:{trade}:read")
    reply = ("مدة إجازة الأمومة عشرة أسابيع مدفوعة الأجر بالكامل وفق نظام العمل الصادر في 2030-01-10، "
             "وبلغت صادرات التمور 812 مليون ريال في عام 2030 وفق تقرير التجارة.")
    events = await _turn(services, reader, "ما مدة إجازة الأمومة وكم بلغت صادرات التمور؟", [labor, trade], reply)
    done = next(e for e in events if e["type"] == "done")["data"]
    assert "kb" in done["state_digest"]["capabilities"]
    cited = {c["document_id"]: c for c in done["citations"]}
    assert set(cited) == {labor_doc["id"], trade_doc["id"]}
    expected = {"title": "نظام العمل", "source": "kb", "kb": labor, "publisher": "وزارة الموارد",
                "published_on": "2030-01-10", "pages": [1]}
    assert {key: cited[labor_doc["id"]].get(key) for key in expected} == expected
    assert cited[labor_doc["id"]]["kb_name"] == {"ar": "أنظمة العمل", "en": f"KB {labor}"}
    assert cited[trade_doc["id"]]["kb_name"]["ar"] == "التجارة" and cited[trade_doc["id"]]["url"]
    # No backend or collection ever leaves in a citation (ADR-0202).
    assert all("backend" not in c and "collection" not in c for c in done["citations"])


async def test_the_latest_version_comes_first_and_an_older_one_is_labelled(services, api, spans):
    slug = _slug()
    await _create(api, slug, name_ar="الاقتصاد الدولي")
    april = await _document(services, api, slug, APRIL, title="آفاق أبريل", published_on="2031-04-15")
    july = await _document(services, api, slug, JULY, title="آفاق يوليو", published_on="2031-07-20",
                           supersedes=april["id"])
    reader = _reader(f"kb:{slug}:read")
    found = await _search(services, reader, [slug], "كم نسبة نمو اقتصاد الواحة المتوقعة في عام 2031؟")
    order = [s["attachment_id"] for s in found.sources]
    assert order[0] == july["id"] and order.count(april["id"]) <= 1
    older = [s for s in found.sources if s["attachment_id"] == april["id"]]
    assert all(s["superseded_by"] == "آفاق يوليو (2031-07-20)" for s in older)
    assert all(s["superseded_by"] is None for s in found.sources if s["attachment_id"] == july["id"])
    # The answer's evidence names each passage's date and the older one's successor.
    swapped, gateway = replacing(services, {"reasoning": "النمو المتوقع 4.5 بالمئة وفق آفاق يوليو (2031-07-20)."})
    request = TurnRequest(conversation_id=str(uuid.uuid4()), principal=reader,
                          message="كم نسبة نمو اقتصاد الواحة المتوقعة في عام 2031؟", locale="ar",
                          selected_kbs=[slug])
    events = [e.model_dump(mode="json") async for e in TurnRunner(swapped).stream(request, "req-kb-v")]
    done = next(e for e in events if e["type"] == "done")["data"]
    assert [c["document_id"] for c in done["citations"]][:1] == [july["id"]]


async def test_a_purge_a_backend_cannot_take_is_owed_and_settled_later(services, api):
    slug = _slug()
    await _create(api, slug)
    await _document(services, api, slug, LABOR, title="نظام العمل")

    class Refusing:
        name = "internal"

        async def purge(self, collection, scope):
            raise RuntimeError("down")

    kbs = services.kbs
    failing = KbService(store=kbs.store, backend=Refusing(), backends={"internal": Refusing()}, limits=kbs.limits)
    deleted = await failing.delete("default", slug, Actor(user_id="kb-admin"))
    assert deleted is not None and deleted[1] is False
    assert slug in [kb.slug for kb in await services.kbs.store.owed_purges()]
    assert await services.kbs.settle_owed_purges() == 0
    assert slug not in [kb.slug for kb in await services.kbs.store.owed_purges()]
    async with services.db.connection() as conn:
        left = await (await conn.execute(
            "SELECT count(*) AS n FROM agent.rag_chunks WHERE collection = %s", (f"kb_{slug}",))).fetchone()
    assert left["n"] == 0


async def test_kb_metadata_dates_are_iso_and_kept(services, api):
    slug = _slug()
    await _create(api, slug)
    view = await _document(services, api, slug, LABOR, title="نظام", published_on="2029-12-31", ingest=False)
    assert date.fromisoformat(view["metadata"]["published_on"]) == date(2029, 12, 31)


async def test_a_report_over_a_kb_labels_its_figures_with_the_kbs_name(services, api):
    # Owner decision A (ADR-0388, ADR-0389), over a capability that returns findings: a figure the KB holds
    # reads «من: <KB name>»; one the findings do not hold reads «غير متوفر» in its place, never general
    # knowledge on a KB turn (ADR-0398). The passage holds 812 with «التمور», the page's label for it (ADR-0399).
    trade = _slug()
    await _create(api, trade, name_ar="التجارة")
    await _document(services, api, trade, TRADE, title="تقرير التجارة", publisher="هيئة الإحصاء", published_on="2031-02-01")
    page = ('<!doctype html><html><head><title>لوحة التمور</title></head><body><main class="kit-page">'
            '<div class="kit-kpi-value">812</div><p>ونمت 7.5 بالمئة.</p></main></body></html>')
    swapped, _ = replacing(services, {"report_writer": page})
    request = TurnRequest(conversation_id=str(uuid.uuid4()), principal=_reader(f"kb:{trade}:read"),
                          message="اعمل لوحة مؤشرات عن صادرات التمور", locale="ar", selected_kbs=[trade], output="report")
    events = [e.model_dump(mode="json") async for e in TurnRunner(swapped).stream(request, "req-kb-report")]
    assert events[-1]["type"] == "done", events[-1]
    html = "".join(e["data"]["text"] for e in events if e["type"] == "artifact_delta")
    assert 'data-kit-source="kb" data-kit-label="من: التجارة" title="من: التجارة">812</span>' in html
    assert 'data-kit-source="unavailable" title="غير متوفر">غير متوفر</span>' in html
    assert "7.5" not in html and "من المعرفة العامة" not in html.split('data-kit="sources"')[0]


async def test_a_registration_keeps_the_files_hash_and_a_missing_metadata_flag_until_corrected(services, api):
    # Owner decision B (ADR-0392): the seed step's idempotence and the admin page's flag.
    slug = _slug()
    await _create(api, slug)
    digest = "ab" * 32
    response = await api.post(
        f"/v1/admin/kbs/{slug}/documents",
        json={"name": "يدوي.pdf", "mime": "application/pdf", "size": 3, "object_key": f"kb/{slug}/{uuid.uuid4()}",
              "metadata": {"title": "يدوي.pdf", "publisher": "جهة", "published_on": "2026-10-29"},
              "sha256": digest, "metadata_missing": True},
        headers=_admin(),
    )
    assert response.status_code == 201, response.text
    view = response.json()
    assert view["sha256"] == digest and view["metadata_missing"] is True
    listed = (await api.get(f"/v1/admin/kbs/{slug}/documents", headers=_admin())).json()["documents"]
    assert [(d["sha256"], d["metadata_missing"]) for d in listed] == [(digest, True)]
    corrected = await api.put(
        f"/v1/admin/kbs/{slug}/documents/{view['id']}/metadata",
        json={"title": "تقرير يدوي", "publisher": "الهيئة", "published_on": "2026-10-01"}, headers=_admin(),
    )
    assert corrected.status_code == 200 and corrected.json()["metadata_missing"] is False
    assert corrected.json()["sha256"] == digest
