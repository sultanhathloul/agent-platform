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


def mark(figure: str, kind: str = "general", label: str = "من المعرفة العامة") -> str:
    return (f'<span class="kit-figure" data-kit="figure" data-kit-source="{kind}" data-kit-label="{label}" '
            f'title="{label}">{figure}</span>')


def test_figures_labelled_needs_every_figure_labelled_and_the_models_own_as_general_knowledge():
    page_ok = page(f'<p>النمو {mark("3.1")} في {mark("2031", "message", "من: طلبك")}</p>')
    assert pages.figures_labelled(page_ok, "لوحة عن 2031")
    assert not pages.figures_labelled(page(f'<p>النمو {mark("3.1")} في 2031</p>'), "لوحة عن 2031")  # 2031 unlabelled
    assert not pages.figures_labelled(page_ok, "لوحة عامة")  # 2031 labelled as the request's, which never held it
    assert not pages.figures_labelled(page(f'<p>{mark("2031", "message", "من: طلبك")}</p>'), "2031")  # no general one
    data = ('<script type="application/json" id="report-data">{"charts":{"g":{"labels":[],"datasets":'
            '[{"label":"x","data":[3.1,4],"sources":["من المعرفة العامة","%s"],"general":[0]}]}}}</script>')
    assert not pages.figures_labelled(page(mark("1") + data % "من المعرفة العامة"), "")  # 4 not general, not given
    assert pages.figures_labelled(page(mark("1") + data % "من: طلبك"), "4")
    assert not pages.figures_labelled(page(mark("1") + data.replace('"%s"', "null")), "4")  # a point with no label


def test_figures_sourced_needs_each_expected_figure_with_its_kb_or_file_label():
    kb = page(f'<div class="kit-kpi-value">{mark("3.6", "kb", "من: اقتصاد الواحة")}</div>'
              f'<p>{mark("٢٫٢", "file", "من: الموازنة.pdf")} و {mark("5.8")}</p>')
    assert pages.figures_sourced(kb, ["3.6", "2.2"])
    assert not pages.figures_sourced(kb, ["5.8"])  # shown, but as general knowledge
    assert pages.figures_sourced(kb, [["9", "3.6"]])
    data = ('<script type="application/json" id="report-data">{"charts":{"g":{"labels":[],"datasets":'
            '[{"label":"x","data":[812,455],"sources":["من: الواحة","من المعرفة العامة"],"general":[1]}]}}}</script>')
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
    named = page(f'<p>الربع Q1 و Q2 و COVID-19 و 5G، و {mark("40")}</p>')
    assert pages.figures_labelled(named, "")


def test_the_development_model_strip_is_not_the_pages_figures():
    # Owner decision C (ADR-0391): its token counts and seconds are platform metadata, not report figures.
    strip = ('<footer class="kit-model-info" data-kit="model-info" lang="ar" dir="rtl"><p>معلومات النموذج</p>'
             "<table><tbody><tr><td>heavy</td><td>38.2 ث</td><td>12,345 / 4,321</td></tr></tbody></table></footer>")
    assert pages.figures_labelled(page(f'<p>{mark("40")}</p>' + strip), "")
