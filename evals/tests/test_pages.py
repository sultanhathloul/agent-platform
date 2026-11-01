"""The reports suite's page checks (reports PR, I8, ADR-0385; option C, ADR-0406 to ADR-0409): each catches
what it gates."""

import json
import sys
from pathlib import Path

EVALS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVALS))

import pages  # noqa: E402

FRAME = '<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><title>t</title></head><body>{body}</body></html>'
CHART = {"kind": "chart", "type": "line", "title": "معدل البطالة", "labels": ["الربع الأول 2026", "الربع الثاني 2026"],
         "series": [{"name": "سعودي", "data": [6.4, 6.5]}], "sources": ["P1"]}


def page(spec: dict | None = None, *, sources: str = "", general: bool = False, body: str = "", locale: str = "ar") -> str:
    spec = spec if spec is not None else {"title": "سوق العمل", "language": locale, "sections": [CHART]}
    note = '<p class="kit-general-note" data-kit="general">من المعرفة العامة</p>' if general else ""
    block = ('<script type="application/json" class="kit-spec" id="report-spec">'
             + json.dumps(spec, ensure_ascii=False).replace("<", "\\u003c") + "</script>")
    out = FRAME.format(body=f'<main class="kit-page">{note}{body}{sources}{block}</main>')
    return out.replace('lang="ar" dir="rtl"', 'lang="en" dir="ltr"', 1) if locale == "en" else out


SOURCES = ('<section class="kit-sources" data-kit="sources" lang="ar" dir="rtl"><h2>المصادر</h2><ol><li>'
           '<div class="kit-source-doc">نشرة سوق العمل</div><ul><li>رسم: معدل البطالة — ص 1</li></ul></li></ol></section>')


def test_parses_needs_a_whole_document_closed_in_order_and_json_blocks():
    assert pages.parses(page(sources=SOURCES))
    assert not pages.parses(page(body="<p>نص</main></p>"))
    assert not pages.parses("<p>no frame</p>")
    broken = page().replace('id="report-spec">{', 'id="report-spec">{bad')
    assert not pages.parses(broken)


def test_direction_follows_the_locale():
    assert pages.direction(page(), "ar") and not pages.direction(page(), "en")
    assert pages.direction(page(locale="en"), "en")


def test_kit_only_refuses_any_outside_reference_and_any_script_but_the_json_blocks():
    clean = page(body='<a href="#s">x</a><script type="application/json" class="kit-chart-data" id="c1">{}</script>')
    assert pages.kit_only(clean, "<style>body{}</style><script>var a=1</script>")
    assert not pages.kit_only(page(body='<img src="https://x.example.com/a.png">'), "")
    assert not pages.kit_only(clean, '<link href="https://cdn.example.com/k.css">')
    assert not pages.kit_only(page(body="<script>alert(1)</script>"), "")


def test_spec_valid_reads_the_stored_spec_against_the_committed_schema():
    assert pages.spec_valid(page())
    assert not pages.spec_valid(page({"title": "t", "language": "ar", "sections": []}))
    assert not pages.spec_valid(page({"title": "t", "language": "ar", "sections": [{**CHART, "sources": ["الهيئة"]}]}))
    assert not pages.spec_valid(page({"title": "t", "language": "ar", "sections": [{**CHART, "colour": "red"}]}))
    assert not pages.spec_valid(FRAME.format(body="<p>no spec</p>"))


def test_no_general_with_findings_and_general_only_without():
    assert pages.no_general(page(sources=SOURCES))
    assert not pages.no_general(page({"title": "t", "language": "ar", "sections": [{**CHART, "sources": ["general"]}]}))
    assert not pages.no_general(page(sources=SOURCES, general=True))
    general = page({"title": "t", "language": "ar", "sections": [{**CHART, "sources": ["general"]}]}, general=True)
    assert pages.general_only(general)
    assert not pages.general_only(general.replace("</main>", SOURCES + "</main>"))
    assert not pages.general_only(page(general=True))  # an element citing a passage


def test_the_sources_section_lists_each_element_under_a_document():
    assert pages.sources_section(page(sources=SOURCES))
    assert not pages.sources_section(page())
    other = {"title": "t", "language": "ar", "sections": [CHART, {**CHART, "title": "معدل المشاركة"}]}
    assert not pages.sources_section(page(other, sources=SOURCES))


def test_figures_sourced_needs_each_expected_figure_kept_in_a_listed_sourced_element():
    assert pages.figures_sourced(page(sources=SOURCES), ["6.5", ["6.4", "6.40"]])
    assert not pages.figures_sourced(page(sources=SOURCES), ["47.1"])
    assert not pages.figures_sourced(page(), ["6.5"])  # the element is not in the sources
    gap = {"title": "t", "language": "ar", "sections": [{**CHART, "series": [{"name": "سعودي", "data": [6.4, None]}]}]}
    assert not pages.figures_sourced(page(gap, sources=SOURCES), ["6.5"])


def test_page_fields_read_the_version_and_the_download_from_the_gateways_envelope():
    import httpx
    import run_evals

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/download"):
            return httpx.Response(200, text="<!doctype html>download")
        return httpx.Response(200, json={"request_id": "r", "data": {"html": "<!doctype html>page"}})

    http = httpx.Client(transport=httpx.MockTransport(answer))
    done = {"state_digest": {"output": "report"}, "artifact": {"id": "a", "kind": "report", "title": "t", "version": 2}}
    fields = run_evals.page_fields(http, "http://gw", "token", done)
    assert fields["page"] == "<!doctype html>page" and fields["download"] == "<!doctype html>download"
    assert fields["form"] == "report" and fields["artifact"]["version"] == 2
    assert run_evals.page_fields(http, "http://gw", "token", {"state_digest": {"output": "text"}})["page"] is None


def test_request_language_reads_the_specs_title_and_labels():
    # Owner decision 4 on PR #17 (ADR-0396); make-eval-20261029T094813Z kb-en-exports and file-en-budget.
    english = {"title": "Oasis exports", "language": "en", "sections": [{**CHART, "title": "Dates", "labels": ["2030"],
                                                                         "series": [{"name": "Exports", "data": [1]}]}]}
    assert pages.request_language(page(english, sources=SOURCES, locale="en"), "en")  # the sources are the platform's
    assert not pages.request_language(page({**english, "title": "تقرير صادرات الواحة"}, locale="en"), "en")
    arabic_series = {**english, "sections": [{**english["sections"][0], "series": [{"name": "الصادرات", "data": [1]}]}]}
    assert not pages.request_language(page(arabic_series, locale="en"), "en")
    assert pages.request_language(page(), "ar")
    assert not pages.request_language(page({"title": "Oasis board", "language": "ar", "sections": [CHART]}), "ar")
