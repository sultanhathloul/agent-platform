"""Option C (owner decisions A and B on PR #17, ADR-0406 to ADR-0408): the report spec, its schema, its
streamed sections, the check of each element against its cited passages, and the page the platform renders."""

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from app.artifacts.config import load_artifacts
from app.artifacts.document import KIT_MARKER, ModelCall, model_strip, served, without_model_info
from app.artifacts.figures import canonical, canonical_number, figures, spans
from app.artifacts.render import PageRenderer, markdown, render_page
from app.artifacts.spec import ReportSpec, SectionReader, SpecError, parse, schema
from app.artifacts.spec_stream import strings, visible_text
from app.artifacts.verify import MISSING, Passage, Verifier
from app.settings import _default_config_dir

CONFIG = _default_config_dir()
ARTIFACTS = load_artifacts(CONFIG)
TEXT = ARTIFACTS.report.text
KINDS = {"kpi_row": "مؤشرات", "chart": "رسم", "table": "جدول"}

# Table 1 of the Q2 2026 labour-market bulletin, as the KB holds it (page 1), and table 3-1 of its workbook.
TABLE_1 = ("جدول 1: المؤشرات الرئيسة لسوق العمل السعودي حسب أرباع السنة المؤشرات الربع الثاني 2025 الربعالاول 2026 "
           "الربعالثاني 2026 ذكور إناث الإجمالي معدل البطالة 2.0 8.7 3.2 2.2 7.2 3.1 2.1 7.6 3.0 سعودي 4.3 11.3 6.8 "
           "4.9 9.0 6.4 4.8 9.6 6.5 معدل المشاركة في القوى العاملة 83.5 35.4 67.1 84.0 34.3 67.2 84.3 34.0 67.2")
TABLE_3_1 = ("التوزيع النسبي للمشتغلين بحسب الجنسية و الجنس(%) | السعوديين | غير السعوديين | الإجمالي | الذكور | الاناث "
             "الربع الثاني 2026 | 66.366373 | 33.633627 | 100 | 91.46388445 | 8.536115554 | 100 | 83.60299013 | 16.39700987 | 100")
P1 = Passage("P1", "r1", TABLE_1, document_id="bulletin", title="إحصاءات سوق العمل للربع الثاني 2026",
             publisher="الهيئة العامة للإحصاء", published_on="2026-09-30",
             kb_name={"ar": "إحصاءات الهيئة العامة للإحصاء", "en": "GASTAT statistics"}, page=1)
P2 = Passage("P2", "r2", TABLE_3_1, document_id="workbook", title="إحصاءات سوق العمل للربع الثاني 2026 (جداول Excel)",
             publisher="الهيئة العامة للإحصاء", published_on="2026-09-30", locator="3-1")


def verifier(*, sourced: bool = True) -> Verifier:
    return Verifier([P1, P2], sourced=sourced, kinds=KINDS, untitled=TEXT.untitled_element.of("ar"))


def spec(*sections, language="ar") -> ReportSpec:
    return ReportSpec.model_validate({"title": "سوق العمل", "language": language, "sections": list(sections)})


UNEMPLOYMENT = {"kind": "chart", "type": "line", "title": "معدل البطالة", "labels": ["الربع الثاني 2025", "الربع الأول 2026"],
                "series": [{"name": "الإجمالي", "data": [3.2, 3.1]}, {"name": "سعوديون", "data": [6.8, 6.4]}], "unit": "%",
                "sources": ["P1"]}
GENDER = {"kind": "chart", "type": "doughnut", "title": "توزيع المشتغلين حسب الجنس", "labels": ["الذكور", "الإناث"],
          "series": [{"name": "الإجمالي", "data": [83.6, 16.4]}], "sources": ["P2"]}


# ------------------------------------------------------------------ the schema and the stream


def test_the_committed_schema_is_the_one_the_models_give():
    committed = json.loads((Path(CONFIG) / "artifacts" / "report-spec.schema.json").read_text(encoding="utf-8"))
    assert committed == json.loads(json.dumps(schema()))


def test_a_spec_parses_from_a_fence_and_anything_else_is_refused():
    text = '```json\n{"title": "ت", "language": "ar", "sections": [{"kind": "text", "markdown": "نص"}]}\n```'
    assert parse(text).sections[0].markdown == "نص"
    for bad in ("أُنشئ التقرير «لوحة الهلال» (الإصدار 1).", '{"title": "ت", "language": "ar", "sections": []}',
                '{"title": "ت", "language": "ar", "sections": [{"kind": "chart", "title": "x"}]}', "<html></html>"):
        with pytest.raises(SpecError):
            parse(bad)


def test_sources_are_passage_ids_or_general_only():
    with pytest.raises(ValueError):
        spec({**UNEMPLOYMENT, "sources": ["P1", "الهيئة"]})
    with pytest.raises(ValueError):
        spec({**UNEMPLOYMENT, "sources": []})


def test_sections_leave_the_reader_as_each_one_closes_whatever_the_chunks():
    text = json.dumps({"title": "ت {", "language": "ar", "sections": [
        {"kind": "text", "markdown": 'قال "مرحبا" {و} [ ]'}, UNEMPLOYMENT]}, ensure_ascii=False)
    for size in (1, 3, 17):
        reader, out = SectionReader(), []
        for at in range(0, len(text), size):
            reader.feed(text[at : at + size])
            out += reader.take()
        assert [getattr(o, "title", None) or o.get("kind") for o in out] == ["ت {", "text", "chart"]


# ------------------------------------------------------------------ the check of each element


@pytest.mark.parametrize("raw,expected", [("1,250", "1250"), ("١٬٢٥٠", "1250"), ("3.60", "3.6"), ("007", "7")])
def test_canonical_figures(raw, expected):
    assert canonical(raw) == expected


def test_names_with_digits_are_not_figures_and_a_workbooks_precision_rounds():
    assert [c for _, _, c in spans("Q1 و COVID-19 و 5G و 3 872")] == ["3872"]
    assert {"83.6", "16.4", "66.37", "66.4"} <= figures([TABLE_3_1])
    assert "3.7" not in figures(["3.65"]) and canonical_number(3.0) == "3"


def test_a_value_counts_only_with_its_label_in_one_of_its_elements_cited_passages():
    v = verifier()
    checked = v.element(spec(UNEMPLOYMENT).sections[0])
    assert [s.data for s in checked.series] == [[3.2, 3.1], [6.8, 6.4]]
    # 84.3 is in Table 1, but as the male participation rate: not with «الذكور» or «التوزيع» in that passage.
    gender_from_table_1 = {**GENDER, "series": [{"name": "الإجمالي", "data": [84.3, 15.7]}], "sources": ["P1"]}
    assert v.element(spec(gender_from_table_1).sections[0]) is None  # nothing found: the element leaves
    assert "توزيع المشتغلين حسب الجنس" in v.result.dropped


def test_the_gender_doughnut_is_found_in_table_3_1_by_its_rounding():
    v = verifier()
    checked = v.element(spec(GENDER).sections[0])
    assert checked.series[0].data == [83.6, 16.4]
    [doc] = v.result.documents
    assert doc.document_id == "workbook" and doc.uses[0].where == ["s3-1"]


def test_a_value_not_found_is_a_gap_listed_under_its_document_and_the_element_stays():
    v = verifier()
    chart = {**UNEMPLOYMENT, "series": [{"name": "سعوديون", "data": [6.8, 47.1]}]}
    checked = v.element(spec(chart).sections[0])
    assert checked.series[0].data == [6.8, None]
    use = v.result.documents[0].uses[0]
    assert use.kind == "رسم" and use.where == ["p1"] and use.unavailable == ["الربع الأول 2026 · سعوديون"]


def test_with_findings_general_is_not_allowed_and_without_them_nothing_is_matched():
    v = verifier()
    assert v.element(spec({**UNEMPLOYMENT, "sources": ["general"]}).sections[0]) is None
    assert v.element(spec({**UNEMPLOYMENT, "sources": ["P9"]}).sections[0]) is None  # not this turn's
    general = verifier(sourced=False)
    checked = general.element(spec({**UNEMPLOYMENT, "sources": ["P1"]}).sections[0])
    assert checked.sources == ["general"] and checked.series[0].data == [3.2, 3.1]
    assert not general.result.documents


def test_kpis_tables_and_prose_are_checked_the_same_way():
    v = verifier()
    kpis = v.element(spec({"kind": "kpi_row", "items": [
        {"label": "معدل البطالة", "value": 3.0, "unit": "%"}, {"label": "عدد المشتغلين", "value": 14.2}], "sources": ["P1"]}).sections[0])
    assert [i.value for i in kpis.items] == [3.0, None]
    table = v.element(spec({"kind": "table", "title": "البطالة", "columns": ["الفئة", "الربع الثاني 2025"],
                            "rows": [["سعودي", 6.8], ["الإجمالي", "3.2%"], ["الشباب", 19.9]], "sources": ["P1"]}).sections[0])
    assert table.rows == [["سعودي", 6.8], ["الإجمالي", "3.2%"], ["الشباب", MISSING]]
    text = v.element(spec({"kind": "text", "markdown": "بلغ معدل البطالة 3.0% وعدد الأندية 18."}).sections[0])
    assert text.markdown == f"بلغ معدل البطالة 3.0% وعدد الأندية {MISSING}."


# ------------------------------------------------------------------ the page


def page_of(s: ReportSpec, *, sourced=True) -> tuple[str, Verifier]:
    v = Verifier([P1, P2], sourced=sourced, kinds=KINDS, untitled=TEXT.untitled_element.of("ar"))
    checked = v.spec(s)
    renderer = PageRenderer("ar", TEXT, sourced=sourced)
    return render_page(checked.spec, renderer, renderer.sources(checked.documents), ""), v


def test_the_page_is_rendered_by_the_platform_deterministically_with_no_label_beside_any_figure():
    s = spec({"kind": "kpi_row", "items": [{"label": "<b>البطالة</b>", "value": 3.0, "unit": "%"}], "sources": ["P1"]},
             UNEMPLOYMENT, GENDER, {"kind": "text", "markdown": "**مهم**: <script>alert(1)</script>"})
    page, _ = page_of(s)
    again, _ = page_of(s)
    assert page == again and page.startswith('<!doctype html><html lang="ar" dir="rtl">') and KIT_MARKER in page
    HTMLParser().feed(page)
    assert "<script>alert" not in page and "&lt;b&gt;البطالة&lt;/b&gt;" in page and "<strong>مهم</strong>" in page
    assert "kit-figure" not in page and "data-kit-label" not in page and "من المعرفة العامة" not in page
    assert page.count('class="kit-chart-data"') == 2 and '<script type="application/json" class="kit-spec"' in page


def test_the_sources_list_each_document_and_under_it_the_elements_it_supports():
    chart = {**UNEMPLOYMENT, "series": [{"name": "سعوديون", "data": [6.8, 47.1]}]}
    page, _ = page_of(spec(chart, GENDER))
    sources = re.search(r'<section class="kit-sources".*?</section>', page, re.S).group(0)
    assert "<h2>المصادر</h2>" in sources
    assert "إحصاءات الهيئة العامة للإحصاء › إحصاءات سوق العمل للربع الثاني 2026 — الهيئة العامة للإحصاء — 2026-09-30" in sources
    assert "رسم: معدل البطالة — ص 1" in sources and "غير متوفر في المصدر: الربع الأول 2026 · سعوديون" in sources
    assert "رسم: توزيع المشتغلين حسب الجنس — ورقة 3-1" in sources


def test_a_general_report_opens_with_one_line_and_has_no_sources_section():
    page, _ = page_of(spec({**UNEMPLOYMENT, "sources": ["general"]}), sourced=False)
    assert "هذا التقرير من المعرفة العامة للنموذج، دون مصادر موثقة" in page and "kit-sources" not in page


def test_an_element_with_nothing_found_leaves_the_page():
    page, v = page_of(spec(UNEMPLOYMENT, {**GENDER, "sources": ["P1"]}))
    assert "معدل البطالة" in page and "توزيع المشتغلين" not in page.split('class="kit-spec"')[0]
    assert v.result.dropped == ["توزيع المشتغلين حسب الجنس"]


def test_markdown_is_a_small_safe_subset():
    assert markdown("- أ\n- **ب**") == "<ul><li>أ</li><li><strong>ب</strong></li></ul>"
    assert markdown("# عنوان\nسطر <i>") == "<p>عنوان<br>سطر &lt;i&gt;</p>"


def test_the_checks_read_every_string_a_person_reads_and_nothing_structural():
    s = spec(UNEMPLOYMENT)
    found = [text for _, text in strings(s.model_dump(mode="json"))]
    assert "معدل البطالة" in found and "سعوديون" in found and "P1" not in found and "line" not in found
    assert "الربع الثاني 2025" in visible_text(s)


# ------------------------------------------------------------------ the served forms and the model strip


def test_the_view_links_the_kit_from_its_path_and_the_download_inlines_it():
    page, _ = page_of(spec(UNEMPLOYMENT))
    view = served(page, ARTIFACTS.kit, form="view", kit_base="http://localhost:8080/report-kit/v1/")
    assert '<script src="http://localhost:8080/report-kit/v1/vendor/echarts.min.js" defer></script>' in view
    download = served(page, ARTIFACTS.kit, form="download")
    assert not re.search(r"""(?:src|href)\s*=\s*["']?(?:https?:)?//""", download)
    assert "data:font/woff2;base64," in download and KIT_MARKER not in download
    csp = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', download).group(1)
    assert "connect-src 'none'" in csp and len(re.findall(r"'sha256-", csp)) == len(ARTIFACTS.kit.scripts)


def test_an_inlined_kit_script_cannot_close_its_element():
    for _, source in ARTIFACTS.kit.scripts:
        assert "</script" not in source.lower()


def test_the_profile_chooses_whether_reports_show_the_model_strip():
    assert load_artifacts(CONFIG, "development").show_model_info is True
    assert load_artifacts(CONFIG, "production").show_model_info is False


def test_the_model_strip_names_each_call_and_is_removed_when_not_shown():
    calls = [ModelCall("كتابة التقرير", "openrouter/google/gemma-4-26b-a4b-it", "heavy", 38.24, 12345, 4321)]
    strip = model_strip(calls, "ar", TEXT.model_info)
    for cell in ("google/gemma-4-26b-a4b-it", "openrouter", "heavy", "38.2 ث", "12,345 / 4,321"):
        assert f"<bdi>{cell}</bdi>" in strip, cell
    s = spec(UNEMPLOYMENT)
    v = Verifier([P1], sourced=True, kinds=KINDS, untitled="")
    renderer = PageRenderer("ar", TEXT, sourced=True)
    checked = v.spec(s)
    with_strip = render_page(checked.spec, renderer, "", strip)
    assert "kit-model-info" in with_strip and "kit-model-info" not in without_model_info(with_strip)
