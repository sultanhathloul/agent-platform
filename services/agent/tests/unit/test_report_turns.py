"""A report turn draws from one kind of source (ADR-0398), names no uncited source, and is an edit of the
conversation's report only when the router says so (ADR-0400)."""

from app.artifacts.writer import named_sources, sourced_turn
from app.graph.router import parse_decision

ROUTE = '{"capabilities": ["general"], "skill": null, "output": "report"%s, "complexity": "low", "risk": "low"}'


def test_the_router_says_edit_only_with_a_current_report_and_only_an_explicit_true_is_one():
    edit = parse_decision(ROUTE % ', "edit": true', ["general"], output=True, current=True)
    new = parse_decision(ROUTE % ', "edit": false', ["general"], output=True, current=True)
    missing = parse_decision(ROUTE % "", ["general"], output=True, current=True)
    word = parse_decision(ROUTE % ', "edit": "yes"', ["general"], output=True, current=True)
    assert edit.edit is True and new.edit is False and missing.edit is False and word.edit is False
    # No report in the conversation: nothing to edit, and the field is not read.
    assert parse_decision(ROUTE % ', "edit": true', ["general"], output=True).edit is None
    assert parse_decision(ROUTE % ', "edit": true', ["general"]).edit is None


def test_a_turn_is_sourced_by_its_own_kbs_files_or_findings_only():
    general = {"capability": "general", "findings": "", "sources": []}
    assert not sourced_turn({"capability_outputs": [general]}, [])
    assert sourced_turn({"capability_outputs": [general], "selected_kbs": ["stats-gov"]}, [])
    assert sourced_turn({"capability_outputs": [general], "turn_attachment_ids": ["a1"]}, [])
    assert sourced_turn({"capability_outputs": [{"capability": "kb", "findings": "notes", "sources": []}]}, [])
    assert sourced_turn({"capability_outputs": [general]}, [{"ref": "c1"}])


STATS = {"kb_name": {"ar": "إحصاءات الهيئة العامة للإحصاء", "en": "GASTAT statistics"},
         "title": "نشرة سوق العمل", "publisher": "الهيئة العامة للإحصاء"}


def test_the_page_names_no_source_this_turn_did_not_cite():
    known = ["إحصاءات الهيئة العامة للإحصاء", "GASTAT statistics", "نشرة سوق العمل", "الهيئة العامة للإحصاء", "IMF"]
    page = "لوحة نادي الهلال\nالمصدر: الهيئة العامة للإحصاء\nعدد الألقاب 66"
    assert named_sources(page, known, []) == ["الهيئة العامة للإحصاء"]
    # Cited this turn: allowed, and so is the KB's name, which holds the publisher's.
    assert named_sources(page, known, [STATS]) == []
    assert named_sources("لوحة سوق العمل من إحصاءات الهيئة العامة للإحصاء", known, [STATS]) == []
    # A name too short to tell from a word is not read as a source.
    assert named_sources("IMF data", known, []) == []


# ------------------------------------------------------------------ an edit only on the report's subject (ADR-0412)


def _router_prompts():
    from app.models.prompts import PromptLibrary
    from app.settings import _default_config_dir

    return PromptLibrary.load(_default_config_dir() / "prompts")


def test_the_router_reads_the_current_reports_title_and_section_titles():
    prompts = _router_prompts()
    request = prompts.render(
        "router.request", "ar", files=[], skills=[], message="أضف رسم دائري لتوزيع العاملين حسب الجنس",
        report_candidate=True, current_report="نادي الهلال السعودي",
        current_sections=["البطولات", "توزيع البطولات حسب المسابقة"],
    ).text
    assert 'current="نادي الهلال السعودي"' in request and "- توزيع البطولات حسب المسابقة" in request
    system = prompts.render(
        "router.system", "ar", capabilities=[], report_candidate=True, current_report="نادي الهلال السعودي",
    ).text
    assert "في موضوع التقرير الحالي" in system and "في موضوع آخر ولو قالت «أضف»" in system


async def test_the_section_titles_come_from_the_latest_versions_stored_spec():
    from types import SimpleNamespace

    from app.graph.nodes.route import _current_sections

    spec = {"sections": [{"kind": "kpi_row", "items": []}, {"kind": "chart", "title": "البطولات"},
                         {"kind": "table", "title": "المراكز"}]}

    class Reports:
        async def latest_in(self, tenant, user, conversation):
            return SimpleNamespace(spec=spec)

    ctx = SimpleNamespace(services=SimpleNamespace(reports=Reports()),
                          principal=SimpleNamespace(tenant_id="t", user_id="u"))
    assert await _current_sections({"conversation_id": "c"}, ctx) == ("البطولات", "المراكز")
    ctx.services.reports = None
    assert await _current_sections({"conversation_id": "c"}, ctx) == ()
