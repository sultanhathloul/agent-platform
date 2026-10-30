"""Checks on a stored report page for the eval harness (reports PR, I8, ADR-0376, ADR-0385; option C,
ADR-0406 to ADR-0409). A page is the stored HTML of the version a turn wrote, read back through the gateway as
its owner; it carries the checked report spec it was rendered from (`<script type="application/json"
class="kit-spec" id="report-spec">`), which the checks read with the page. Standard library only: the spec is
checked against config/artifacts/report-spec.schema.json by the small validator below, which covers the
keywords that schema uses."""

import json
import re
from html.parser import HTMLParser
from pathlib import Path

SCHEMA = json.loads((Path(__file__).resolve().parent.parent / "config" / "artifacts" / "report-spec.schema.json")
                    .read_text(encoding="utf-8"))
# Any reference that leaves the page: a scheme-relative or absolute URL in an attribute or a CSS url().
EXTERNAL = re.compile(
    r"""(?:\b(?:src|href|action|data|poster|srcset)\s*=\s*["']?\s*(?:https?:)?//)|(?:url\(\s*["']?\s*(?:https?:)?//)|@import""",
    re.I,
)
FIGURE = re.compile(r"[0-9٠-٩]{1,3}(?:[,٬\u202f\u00a0][0-9٠-٩]{3})+(?:[.٫][0-9٠-٩]+)?|[0-9٠-٩]+(?:[.٫][0-9٠-٩]+)?")
_INDIC = str.maketrans({**{a: str(i) for i, a in enumerate("٠١٢٣٤٥٦٧٨٩")}, "٫": ".", "٬": ",", "\u202f": ",", "\u00a0": ","})
_SOURCES = re.compile(r'<section class="kit-sources" data-kit="sources"[^>]*>.*?</section>', re.S)
_MODEL_INFO = re.compile(r'<footer class="kit-model-info" data-kit="model-info"[^>]*>.*?</footer>', re.S)
_SPEC = re.compile(r'<script type="application/json" class="kit-spec" id="report-spec">(.*?)</script>', re.S)
_DATA = re.compile(r'<script type="application/json" class="kit-(?:chart-data|spec)" id="[^"]*">(.*?)</script>', re.S)
_GENERAL = re.compile(r'<p class="kit-general-note" data-kit="general">')
_ARABIC = re.compile(r"[\u0600-\u06ff\u0750-\u077f\ufb50-\ufdff\ufe70-\ufeff]")
_ELEMENTS = ("kpi_row", "chart", "table")


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
    """A complete document: doctype, one html with head and body, every element closed in order, and every
    JSON block (each chart's data, the spec) JSON."""
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
    """No reference outside the page, in the stored page and in the download; no script but its JSON blocks
    and, in the download, the kit's own inlined ones."""
    if EXTERNAL.search(page) or (download is not None and EXTERNAL.search(download)):
        return False
    scripts = re.findall(r"<script\b[^>]*>", page)
    return all(re.fullmatch(r'<script type="application/json" class="kit-(?:chart-data|spec)" id="[^"]+">', t) for t in scripts)


def spec_of(page: str) -> dict | None:
    match = _SPEC.search(page)
    if not match:
        return None
    try:
        spec = json.loads(match.group(1))
    except ValueError:
        return None
    return spec if isinstance(spec, dict) else None


def _valid(value, schema: dict, defs: dict) -> bool:
    if "$ref" in schema:
        return _valid(value, defs[schema["$ref"].rsplit("/", 1)[-1]], defs)
    for key in ("anyOf", "oneOf"):
        if key in schema:
            matched = sum(1 for option in schema[key] if _valid(value, option, defs))
            if (key == "anyOf" and not matched) or (key == "oneOf" and matched != 1):
                return False
    if "const" in schema and value != schema["const"]:
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    kind = schema.get("type")
    checks = {"string": lambda v: isinstance(v, str), "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
              "number": lambda v: isinstance(v, int | float) and not isinstance(v, bool), "null": lambda v: v is None,
              "array": lambda v: isinstance(v, list), "object": lambda v: isinstance(v, dict), "boolean": lambda v: isinstance(v, bool)}
    if kind and not checks[kind](value):
        return False
    if isinstance(value, str):
        if len(value) > schema.get("maxLength", len(value)) or ("pattern" in schema and not re.search(schema["pattern"], value)):
            return False
    if isinstance(value, list):
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", len(value)):
            return False
        if "items" in schema and not all(_valid(item, schema["items"], defs) for item in value):
            return False
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if any(key not in value for key in schema.get("required", [])):
            return False
        if schema.get("additionalProperties") is False and any(key not in properties for key in value):
            return False
        if not all(_valid(value[key], sub, defs) for key, sub in properties.items() if key in value):
            return False
    return True


def spec_valid(page: str) -> bool:
    """The stored spec matches config/artifacts/report-spec.schema.json."""
    spec = spec_of(page)
    return spec is not None and _valid(spec, SCHEMA, SCHEMA.get("$defs", {}))


def _elements(spec: dict | None) -> list[dict]:
    return [s for s in (spec or {}).get("sections") or [] if s.get("kind") in _ELEMENTS]


def _values(section: dict) -> list:
    if section["kind"] == "kpi_row":
        return [v for i in section.get("items") or [] for v in (i.get("value"), i.get("delta"))]
    if section["kind"] == "chart":
        return [v for s in section.get("series") or [] for v in s.get("data") or []]
    return [c for row in section.get("rows") or [] for c in row[1:]]


def _sources_text(page: str) -> str:
    match = _SOURCES.search(page)
    return re.sub(r"<[^>]+>", " ", match.group(0)) if match else ""


def sources_section(page: str) -> bool:
    """A sourced report: «المصادر» lists each element the spec keeps under a document (ADR-0408)."""
    text = _sources_text(page)
    elements = _elements(spec_of(page))
    return bool(text and elements) and all((s.get("title") or "") in text for s in elements if s.get("title"))


def no_general(page: str) -> bool:
    """With knowledge bases or files: no element cites "general" and the page has no general-knowledge line."""
    elements = _elements(spec_of(page))
    return bool(elements) and not _GENERAL.search(page) and all("general" not in s.get("sources", []) for s in elements)


def general_only(page: str) -> bool:
    """Without findings: the general-knowledge line, no sources section, every element cites "general" only."""
    elements = _elements(spec_of(page))
    return (bool(_GENERAL.search(page)) and not _SOURCES.search(page)
            and all(s.get("sources") == ["general"] for s in elements))


def figures_sourced(page: str, expected: list) -> bool:
    """Each expected figure (or one of its alternatives) is a value the platform kept in a sourced element, so
    one of that element's cited passages holds it with its label (ADR-0407), and the element is listed in the
    sources."""
    text = _sources_text(page)
    kept: set[str] = set()
    for section in _elements(spec_of(page)):
        if "general" in section.get("sources", []) or (section.get("title") and section["title"] not in text):
            continue
        for value in _values(section):
            if isinstance(value, int | float) and not isinstance(value, bool):
                kept.add(canonical(repr(float(value)).removesuffix(".0")))
            elif isinstance(value, str):
                kept.update(canonical(f) for f in figures_in(value))
    for item in expected or []:
        options = [item] if isinstance(item, str) else list(item)
        if not any(canonical(o) in kept for o in options):
            return False
    return bool(expected)


def naming_texts(page: str) -> list[tuple[str, str]]:
    """(kind, text) of what names and labels the report: its title, section titles, KPI labels, chart
    categories and series, table columns and row labels, from its spec."""
    spec = spec_of(page) or {}
    found = [("title", spec.get("title") or "")]
    for s in spec.get("sections") or []:
        found.append(("heading", s.get("title") or ""))
        found += [("label", i.get("label") or "") for i in s.get("items") or []]
        found += [("label", str(x)) for x in s.get("labels") or []]
        found += [("label", x.get("name") or "") for x in s.get("series") or []]
        found += [("label", str(x)) for x in s.get("columns") or []]
        found += [("label", str(row[0])) for row in s.get("rows") or [] if row and isinstance(row[0], str)]
    return [(k, t) for k, t in found if t.strip()]


def request_language(page: str, locale: str) -> bool:
    """Owner decision 4 on PR #17 (ADR-0396): a report's title and labels are in the request's language
    whatever its sources' language. English: none holds an Arabic letter. Arabic: the title is Arabic."""
    texts = naming_texts(page)
    if not texts:
        return False
    if locale == "en":
        return not any(_ARABIC.search(text) for _, text in texts)
    return all(_ARABIC.search(text) for kind, text in texts if kind == "title")
