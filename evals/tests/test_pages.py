"""The reports suite's page checks (reports PR, I8, ADR-0385): each catches what it gates."""

import sys
from pathlib import Path

EVALS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVALS))

import pages  # noqa: E402

FRAME = '<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><title>t</title></head><body>{body}</body></html>'
SOURCES = '<section class="kit-sources" data-kit="sources" lang="ar" dir="rtl"><h2>المصادر</h2><p>ملاحظة 2031</p></section>'


def page(body: str, locale: str = "ar") -> str:
    out = FRAME.format(body=body + SOURCES)
    return out.replace('lang="ar" dir="rtl"', 'lang="en" dir="ltr"', 1) if locale == "en" else out


def test_parses_needs_a_whole_document_closed_in_order_and_json_data():
    assert pages.parses(page("<main><p>نص</p></main>"))
    assert not pages.parses(page("<main><p>نص</main></p>"))
    assert not pages.parses("<p>no frame</p>")
    assert not pages.parses(page('<script type="application/json" id="report-data">{bad</script>'))


def test_direction_follows_the_locale():
    assert pages.direction(page("<p>x</p>"), "ar") and not pages.direction(page("<p>x</p>"), "en")
    assert pages.direction(page("<p>x</p>", "en"), "en")


def test_kit_only_refuses_any_outside_reference_in_the_page_or_the_download():
    clean = page('<a href="#s">x</a><script type="application/json" id="report-data">{}</script>')
    assert pages.kit_only(clean, "<style>body{}</style><script>var a=1</script>")
    assert not pages.kit_only(page('<img src="https://x.example.com/a.png">'), "")
    assert not pages.kit_only(page('<p style="background:url(//x.example.com/a)">x</p>'), "")
    assert not pages.kit_only(clean, '<link href="https://cdn.example.com/k.css">')
    assert not pages.kit_only(page("<script>alert(1)</script>"), "")


def test_figures_marked_needs_every_figure_outside_the_message_marked():
    marked = page('<p>النمو <span class="kit-unverified" data-kit="unverified">3.1</span> في 2031</p>')
    assert pages.figures_marked(marked, "لوحة عن 2031")
    assert not pages.figures_marked(marked, "لوحة عامة")  # 2031 is the model's own and unmarked
    data = ('<script type="application/json" id="report-data">{"charts":{"g":{"labels":[],"datasets":'
            '[{"label":"x","data":[3.1,4],"unverified":[0]}]}}}</script>')
    assert not pages.figures_marked(page('<span class="kit-unverified" data-kit="unverified">1</span>' + data), "")
    assert pages.figures_marked(page('<span class="kit-unverified" data-kit="unverified">1</span>' + data), "4")


def test_figures_sourced_needs_each_expected_figure_shown_and_unmarked():
    shown = page('<div class="kit-kpi-value">3.6</div><p>٢٫٢ و <span class="kit-unverified" data-kit="unverified">5.8</span></p>')
    assert pages.figures_sourced(shown, ["3.6", "2.2"])
    assert not pages.figures_sourced(shown, ["5.8"])
    assert pages.figures_sourced(shown, [["9", "3.6"]])
    data = ('<script type="application/json" id="report-data">{"charts":{"g":{"labels":[],"datasets":'
            '[{"label":"x","data":[812,455],"unverified":[1]}]}}}</script>')
    assert pages.figures_sourced(page(data), ["812"]) and not pages.figures_sourced(page(data), ["455"])


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


def test_names_with_digits_are_not_figures_for_the_check_either():
    # make-eval-20261028T025931Z, general-ar-picker: chart labels "Q1".."Q4" are names (ADR-0381).
    named = page('<p>الربع Q1 و Q2 و COVID-19 و 5G، و <span class="kit-unverified" data-kit="unverified">40</span></p>')
    assert pages.figures_marked(named, "")
