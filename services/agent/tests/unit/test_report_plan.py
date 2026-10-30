"""A report's section plan (ADR-0403) and a report turn's searches in the kb capability (ADR-0401 to ADR-0403)."""

from app.artifacts.plan import MAX_SECTIONS, headings, parse_plan
from app.capabilities.contract import CapabilityInput
from app.capabilities.kb.graph import _cited, _searches


def test_the_plan_is_up_to_eight_distinct_queries_and_anything_unusable_is_none():
    text = '{"sections": [{"title": "البطالة", "query": "معدل البطالة حسب الربع"}, {"query": "معدل البطالة حسب الربع"},'
    text += ' {"title": "الجنس", "query": "التوزيع النسبي للمشتغلين حسب الجنس"}, {"title": 3}]}'
    assert parse_plan(text) == ["معدل البطالة حسب الربع", "التوزيع النسبي للمشتغلين حسب الجنس"]
    many = '{"sections": [' + ",".join(f'{{"query": "q{i}"}}' for i in range(12)) + "]}"
    assert len(parse_plan(many)) == MAX_SECTIONS
    assert parse_plan("no json") == [] and parse_plan('{"sections": "x"}') == []


def test_an_edits_plan_reads_the_current_versions_title_headings_and_chart_titles():
    page = ('<title>داشبورد سوق العمل</title><h1>داشبورد سوق العمل</h1><h2>معدل <b>البطالة</b></h2>'
            '<script type="application/json" id="report-data">{"charts":{"u":{"title":"معدل المشاركة"}}}</script>')
    assert headings(page) == ["داشبورد سوق العمل", "معدل البطالة", "معدل المشاركة"]


def _input(**extra) -> CapabilityInput:
    return CapabilityInput.model_validate(
        {"message": "سوق العمل", "principal": {"user_id": "u"}, "cap_state": {}, "locale": "ar", **extra}
    )


RETRIEVAL = {"top_k": 8, "report_top_k": 12, "report_section_k": 4, "report_max_passages": 40}


def test_a_report_searches_the_request_the_report_and_each_planned_section_within_its_budget():
    assert _searches(_input(), RETRIEVAL) == ([("سوق العمل", 8)], 8)
    searches, budget = _searches(
        _input(output="report", report_title="داشبورد", report_queries=["البطالة", "الجنس"]), RETRIEVAL
    )
    assert searches == [("سوق العمل", 12), ("داشبورد", 4), ("البطالة", 4), ("الجنس", 4)] and budget == 40


def test_an_edit_returns_the_passages_its_version_cited_of_its_own_kind():
    cited = {"id": "c1", "ref": "c1", "title": "نشرة", "kind": "kb", "kb": "stats-gov", "document_id": "d1",
             "collection": "kb_stats-gov"}
    request = _input(output="report", report_sources=[cited, {**cited, "id": "f1", "ref": "f1", "kind": "doc"}])
    assert [s.ref for s in _cited(request)] == ["c1"]
