"""Checks on a stored report page for the eval harness (reports PR, I8, ADR-0376, ADR-0385). Generic over
output forms: a page is the stored HTML of the version a turn wrote, read back through the gateway as its
owner. Standard library only."""

import json
import re
from html.parser import HTMLParser

# Any reference that leaves the page: a scheme-relative or absolute URL in an attribute or a CSS url().
EXTERNAL = re.compile(
    r"""(?:\b(?:src|href|action|data|poster|srcset)\s*=\s*["']?\s*(?:https?:)?//)|(?:url\(\s*["']?\s*(?:https?:)?//)|@import""",
    re.I,
)
FIGURE = re.compile(r"[0-9٠-٩]{1,3}(?:[,٬][0-9٠-٩]{3})+(?:[.٫][0-9٠-٩]+)?|[0-9٠-٩]+(?:[.٫][0-9٠-٩]+)?")
_INDIC = str.maketrans({**{a: str(i) for i, a in enumerate("٠١٢٣٤٥٦٧٨٩")}, "٫": ".", "٬": ","})
# A figure in its source label's marker (owner decision A, ADR-0388): (kind, label, figure).
_MARKED = re.compile(
    r'<span class="kit-figure" data-kit="figure" data-kit-source="([a-z]+)" data-kit-label="([^"]*)" title="[^"]*">(.*?)</span>',
    re.S,
)
_SVG_MARKED = re.compile(r'([0-9٠-٩][0-9٠-٩.,٫٬]*)<tspan class="kit-figure-svg" data-kit-source="([a-z]+)">[^<]*</tspan>')
_SOURCES = re.compile(r'<section class="kit-sources" data-kit="sources"[^>]*>.*?</section>', re.S)
_MODEL_INFO = re.compile(r'<footer class="kit-model-info" data-kit="model-info"[^>]*>.*?</footer>', re.S)
_DATA = re.compile(r'<script type="application/json" id="report-data">(.*?)</script>', re.S)


def figures_in(text: str) -> list[str]:
    """The figures of a text, by the platform's own rule (ADR-0381): a figure glued to a word is a name
    ("Q1", "COVID-19", "5G"), not a figure."""
    out = []
    for m in FIGURE.finditer(text):
        start, end = m.start(), m.end()
        before = text[start - 1] if start else ""
        named = (end < len(text) and text[end].isascii() and text[end].isalpha()) or before.isalpha() or (
            before in "-‐‑" and start >= 2 and text[start - 2].isalpha())
        if not named:
            out.append(m.group(0))
    return out


def canonical(raw: str) -> str:
    text = raw.translate(_INDIC).replace(",", "")
    whole, _, fraction = text.partition(".")
    whole = whole.lstrip("0") or "0"
    fraction = fraction.rstrip("0")
    return f"{whole}.{fraction}" if fraction else whole


class _Structure(HTMLParser):
    """Element nesting as a browser would close it: void elements need no end, and an end tag that closes
    elements still open counts as a fault."""

    VOID = {"meta", "br", "hr", "img", "col", "wbr", "link", "input", "source", "area", "base", "embed"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.faults = 0
        self.texts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        return

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        if tag in self.VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.faults += 1
            if tag in self.stack:
                while self.stack and self.stack.pop() != tag:
                    pass
            return
        self.stack.pop()

    def handle_data(self, data):
        if not self._skip:
            self.texts.append(data)


def parses(page: str) -> bool:
    """A complete document: doctype, one html with head and body, every element closed in order, and a
    report-data block, when there is one, that is JSON."""
    if not re.match(r"(?is)\s*<!doctype html>\s*<html\b[^>]*>\s*<head>.*</head>\s*<body>.*</body>\s*</html>\s*$", page):
        return False
    structure = _Structure()
    structure.feed(page)
    structure.close()
    if structure.faults or structure.stack:
        return False
    for block in _DATA.findall(page):
        try:
            json.loads(block)
        except ValueError:
            return False
    return True


def direction(page: str, locale: str) -> bool:
    lang, dir_ = ("en", "ltr") if locale == "en" else ("ar", "rtl")
    return bool(re.match(rf'(?is)\s*<!doctype html>\s*<html lang="{lang}" dir="{dir_}">', page))


def kit_only(page: str, download: str | None) -> bool:
    """No reference outside the page, in the stored page and in the download; no script but the data block
    and, in the download, the kit's own inlined ones."""
    if EXTERNAL.search(page) or (download is not None and EXTERNAL.search(download)):
        return False
    scripts = re.findall(r"<script\b[^>]*>", page)
    return all(tag == '<script type="application/json" id="report-data">' for tag in scripts)


def sources_section(page: str) -> bool:
    return bool(_SOURCES.search(page))


def _body_text(page: str) -> str:
    """The page's visible body text, outside the sources section, the development model strip, scripts and
    styles."""
    body = _MODEL_INFO.sub("", _SOURCES.sub("", page))
    body = re.sub(r"(?is)<head>.*?</head>", "", body)
    structure = _Structure()
    structure.feed(body)
    return "\n".join(structure.texts)


def _marks(page: str) -> list[tuple[str, str]]:
    """(kind, canonical figure) of every labelled figure, text and SVG."""
    found = [(kind, canonical(figure)) for kind, _, figure in _MARKED.findall(page)]
    return found + [(kind, canonical(figure)) for figure, kind in _SVG_MARKED.findall(page)]


def _unmarked(page: str) -> str:
    return _SVG_MARKED.sub("", _MARKED.sub("", page))


def _charts(block: str):
    for chart in (json.loads(block).get("charts") or {}).values():
        yield chart


def figures_labelled(page: str, message: str) -> bool:
    """A general-knowledge page (owner decision A): every figure the body shows carries exactly one label,
    those the user's message does not hold read general knowledge, and at least one does; every chart
    point carries its source the same way."""
    given = {canonical(m) for m in figures_in(message)}
    marks = _marks(page)
    stray = figures_in(_body_text(_unmarked(page)))
    if stray or any(kind != "general" and figure not in given for kind, figure in marks):
        return False
    if '<span class="kit-figure"' in _MARKED.sub("", page):  # a marker inside a marker, or one malformed
        return False
    for block in _DATA.findall(page):
        try:
            charts = list(_charts(block))
        except ValueError:
            return False
        for chart in charts:
            for dataset in chart.get("datasets") or []:
                sources = dataset.get("sources") or []
                general = set(dataset.get("general") or [])
                for index, value in enumerate(dataset.get("data") or []):
                    if not isinstance(value, int | float):
                        continue
                    if index >= len(sources) or not sources[index]:
                        return False
                    if index not in general and canonical(repr(float(value)).removesuffix(".0")) not in given:
                        return False
    return any(kind == "general" for kind, _ in marks)


def figures_sourced(page: str, expected: list) -> bool:
    """Each expected figure (or one of its alternatives) is shown with the label of the knowledge base or
    file that holds it (ADR-0388), in the text or as a chart point."""
    sourced = {figure for kind, figure in _marks(page) if kind in ("kb", "file")}
    for block in _DATA.findall(page):
        try:
            for chart in _charts(block):
                for dataset in chart.get("datasets") or []:
                    general = set(dataset.get("general") or [])
                    sourced |= {canonical(repr(float(v)).removesuffix(".0")) for i, v in enumerate(dataset.get("data") or [])
                                if isinstance(v, int | float) and i not in general}
        except ValueError:
            return False
    for item in expected or []:
        options = [item] if isinstance(item, str) else list(item)
        if not any(canonical(o) in sourced for o in options):
            return False
    return bool(expected)
