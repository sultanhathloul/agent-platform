"""The report library's text forms (report library PR, owner decisions 1 to 3; ADR-0430 to ADR-0432): the
digest every turn lists, the readable text report_read returns, and the indexed text, all from a stored
spec, linear and size-capped."""

from app.artifacts.library import (
    INDEX_TEXT_MAX,
    ReadConfig,
    digest,
    index_text,
    readable,
    source_kinds,
    source_names,
)

SPEC = {
    "title": "إحصاءات سوق العمل",
    "subtitle": "الربع الثاني 2026",
    "language": "ar",
    "sections": [
        {"kind": "kpi_row", "items": [{"label": "معدل البطالة للسعوديين", "value": 6.5, "unit": "%"},
                                      {"label": "المشاركة في قوة العمل", "value": 51.3, "unit": "%", "delta": -0.4}],
         "sources": ["P1"]},
        {"kind": "table", "title": "البطالة حسب الجنس", "columns": ["الفئة", "النسبة"],
         "rows": [["ذكور", 4.8], ["إناث", 9.6]], "sources": ["P2"]},
        {"kind": "chart", "type": "line", "title": "البطالة عبر الأرباع", "labels": ["2025 Q4", "2026 Q1"],
         "series": [{"name": "السعوديون", "data": [7.0, None]}], "sources": ["P3"]},
        {"kind": "text", "title": "ملاحظات", "markdown": "انخفض المعدل.", "sources": []},
    ],
}
CITATIONS = [{"id": "d1", "title": "نشرة سوق العمل", "source": "kb", "kb": "stats-gov",
              "kb_name": {"ar": "إحصاءات الهيئة العامة للإحصاء", "en": "GASTAT statistics"},
              "publisher": "الهيئة العامة للإحصاء", "published_on": "2026-09-30", "pages": [3, 4]}]


def test_the_digest_lists_sections_kpi_values_and_table_headers_never_the_rows():
    text = digest("r1", "إحصاءات سوق العمل", 3, SPEC, "ar").text(900)
    assert "معدل البطالة للسعوديين: 6.5%" in text and "المشاركة في قوة العمل: 51.3% (Δ -0.4)" in text
    assert "«البطالة حسب الجنس» (الفئة | النسبة; 2)" in text
    assert "«البطالة عبر الأرباع»" in text and "الربع الثاني 2026" in text
    assert "ذكور" not in text  # a table's rows are report_read's, not the digest's


def test_the_digest_is_cut_to_its_size():
    big = {**SPEC, "sections": SPEC["sections"] * 30}
    assert len(digest("r1", "t", 1, big, "ar").text(300)) <= 300


def test_readable_text_holds_every_value_table_row_chart_point_and_source():
    text = readable(title="إحصاءات سوق العمل", version=2, spec=SPEC, citations=CITATIONS, locale="ar",
                    config=ReadConfig(max_chars=16000, max_table_rows=60))
    assert text.startswith("# إحصاءات سوق العمل (الإصدار 2)")
    assert "| ذكور | 4.8 |" in text and "| إناث | 9.6 |" in text
    assert "2025 Q4: 7" in text and "2026 Q1: —" in text
    assert "انخفض المعدل." in text
    assert "## المصادر" in text and "نشرة سوق العمل" in text and "ص 3, 4" in text


def test_readable_text_is_cut_with_a_marker_and_table_rows_are_capped():
    rows = [[f"صف {i}", i] for i in range(100)]
    spec = {**SPEC, "sections": [{"kind": "table", "title": "كبير", "columns": ["أ", "ب"], "rows": rows, "sources": ["P1"]}]}
    capped = readable(title="t", version=1, spec=spec, citations=[], locale="ar", config=ReadConfig(max_chars=16000, max_table_rows=5))
    assert "| صف 4 | 4 |" in capped and "| صف 5 |" not in capped and "(+95)" in capped
    cut = readable(title="t", version=1, spec=spec, citations=[], locale="en", config=ReadConfig(max_chars=1000, max_table_rows=100))
    assert len(cut) <= 1000 and cut.endswith("[… cut]")


def test_a_general_knowledge_report_says_so():
    text = readable(title="t", version=1, spec=SPEC, citations=[], locale="ar", config=ReadConfig(max_chars=16000, max_table_rows=60), general=True)
    assert "من المعرفة العامة" in text


def test_the_index_text_is_normalized_and_names_kpis_columns_and_sources():
    text = index_text("إحصاءات سوق العمل", SPEC, CITATIONS)
    for word in ("احصاءات", "سوق", "بطاله", "مشاركه", "نسبه", "هيئه"):
        assert word in text.split(), word
    assert len(index_text("t", {**SPEC, "sections": SPEC["sections"] * 30}, CITATIONS * 50)) <= INDEX_TEXT_MAX


def test_source_kinds_name_each_kb_a_file_or_general_knowledge():
    assert source_kinds(CITATIONS, 0) == ["kb:stats-gov"]
    assert source_kinds([{"title": "budget.xlsx", "source": "doc"}], 0) == ["file"]
    assert source_kinds([], 12) == ["general"]
    assert source_names(CITATIONS)[:2] == ["إحصاءات الهيئة العامة للإحصاء", "GASTAT statistics"]
