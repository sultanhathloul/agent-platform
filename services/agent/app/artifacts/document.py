"""A report page from the sanitized pieces (reports PR, ADR-0380 to ADR-0382).

The platform writes the frame: `<!doctype html>`, `<html lang dir>` from the turn's locale (Arabic:
dir="rtl"), the charset and viewport, the kit marker, then the model's title, styles and body. The
same renderer produces the live deltas and the stored page, so what streams is what is kept, plus
what the platform appends at the end (the sources section). The kit is never stored in the page:
`KIT_MARKER` stands where it goes, and `served` puts the kit's links there for the sandboxed view
or inlines the whole kit for a download that opens offline (I6)."""

import html
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.artifacts.config import Kit, ReportText
from app.artifacts.figures import FigureLabels, Label, canonical_number, spans
from app.artifacts.html import (
    Data,
    Markup,
    Piece,
    Style,
    Text,
    render_data,
    render_style,
    render_text,
    start_tag,
)

KIT_MARKER = "<!--report-kit-->"
CHART_TYPES = frozenset({"bar", "horizontalBar", "stackedBar", "line", "area", "pie", "doughnut", "radar"})
MAX_CHARTS = 24
MAX_POINTS = 500
_HEAD_TAGS = frozenset({"title", "meta"})

# The download's own policy, as the view's headers (I4): no network, the kit's scripts only by hash.
DOWNLOAD_CSP = (
    "default-src 'none'; script-src {hashes}; style-src 'unsafe-inline'; font-src data:; img-src data:; "
    "connect-src 'none'; form-action 'none'; base-uri 'none'"
)


def direction(locale: str) -> tuple[str, str]:
    return ("en", "ltr") if locale == "en" else ("ar", "rtl")


def prologue(locale: str) -> str:
    lang, dir_ = direction(locale)
    return (
        f'<!doctype html><html lang="{lang}" dir="{dir_}"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">{KIT_MARKER}'
    )


def _clean_number(value) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return value if value == value and abs(value) < 1e15 else None  # NaN and absurd values dropped


def normalize_data(raw: str) -> dict:
    """The report-data block reduced to what the kit renders: `charts`, each with a known type, a title,
    a unit, labels and datasets of numbers. Anything else is dropped; invalid JSON is an empty block."""
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {"charts": {}}
    charts = parsed.get("charts") if isinstance(parsed, dict) else None
    out: dict = {}
    for chart_id, spec in list(charts.items())[:MAX_CHARTS] if isinstance(charts, dict) else []:
        if not isinstance(spec, dict) or not isinstance(chart_id, str):
            continue
        kind = spec.get("type") if spec.get("type") in CHART_TYPES else "bar"
        labels = [str(label) if not isinstance(label, int | float) else str(label) for label in (spec.get("labels") or [])][:MAX_POINTS]
        datasets = []
        for item in spec.get("datasets") or []:
            if not isinstance(item, dict):
                continue
            datasets.append(
                {
                    "label": str(item.get("label") or ""),
                    "data": [_clean_number(v) for v in (item.get("data") or [])][:MAX_POINTS],
                }
            )
        chart = {"type": kind, "title": str(spec.get("title") or ""), "labels": labels, "datasets": datasets[:8]}
        if isinstance(spec.get("unit"), str):
            chart["unit"] = spec["unit"][:12]
        if spec.get("stacked") is True:
            chart["stacked"] = True
        out[chart_id[:64]] = chart
    return {"charts": out}


def data_strings(data: dict) -> list[tuple[tuple, str]]:
    """(path, text) of every string a person reads in the block: titles, units, labels, dataset labels."""
    found: list[tuple[tuple, str]] = []
    for chart_id, chart in data["charts"].items():
        found.append(((chart_id, "title"), chart["title"]))
        if "unit" in chart:
            found.append(((chart_id, "unit"), chart["unit"]))
        found += [((chart_id, "labels", i), label) for i, label in enumerate(chart["labels"])]
        found += [((chart_id, "datasets", i, "label"), d["label"]) for i, d in enumerate(chart["datasets"])]
    return found


def set_data_string(data: dict, path: tuple, value: str) -> None:
    chart = data["charts"][path[0]]
    if path[1] in ("title", "unit"):
        chart[path[1]] = value
    elif path[1] == "labels":
        chart["labels"][path[2]] = value
    else:
        chart["datasets"][path[2]]["label"] = value


@dataclass
class FigureCount:
    total: int = 0
    # Labelled general knowledge: matched to no source (D2, ADR-0388).
    general: int = 0
    general_figures: list[str] = field(default_factory=list)
    # The figures the page shows that a source holds, with their labels: a follow-up keeps them (D4).
    sourced: dict[str, Label] = field(default_factory=dict)

    def add(self, figure: str, label: Label) -> None:
        self.total += 1
        if label.kind == "general":
            self.general += 1
            self.general_figures.append(figure)
        else:
            self.sourced.setdefault(figure, label)


def _label(figure: str, labels: FigureLabels, count: FigureCount) -> Label:
    label = labels.of(figure)
    count.add(figure, label)
    return label


def mark_data(data: dict, labels: FigureLabels, count: FigureCount) -> dict:
    """Gives each point, and each axis label that holds a figure, its source label (ADR-0388): the kit
    shows them in the chart's tooltips and in a note under it, and draws general-knowledge points dashed."""
    for chart in data["charts"].values():
        label_sources: list[str | None] = []
        for text in chart["labels"]:
            found = [_label(c, labels, count) for _, _, c in spans(text)]
            label_sources.append(" · ".join(dict.fromkeys(f.text for f in found)) or None)
        if any(label_sources):
            chart["label_sources"] = label_sources
        for dataset in chart["datasets"]:
            sources: list[str | None] = []
            general: list[int] = []
            for i, value in enumerate(dataset["data"]):
                if value is None:
                    sources.append(None)
                    continue
                label = _label(canonical_number(value), labels, count)
                sources.append(label.text)
                if label.kind == "general":
                    general.append(i)
            dataset["sources"] = sources
            if general:
                dataset["general"] = general
    return data


def figure_mark(raw: str, label: Label) -> str:
    """A figure in its label's marker: the label reads after it (kit.css), and on hover."""
    text = html.escape(label.text)
    return (
        f'<span class="kit-figure" data-kit="figure" data-kit-source="{label.kind}" '
        f'data-kit-label="{text}" title="{text}">{raw}</span>'
    )


def mark_text(text: str, where: str, labels: FigureLabels, count: FigureCount) -> str:
    """The text, escaped, with each figure in its source label's marker (ADR-0388). Inside an SVG
    drawing, where no span can wrap it, the figure is followed by its label's words."""
    if where == "title":
        return render_text(text)
    out, at = [], 0
    for start, end, figure in spans(text):
        label = _label(figure, labels, count)
        out.append(render_text(text[at:start]))
        raw = render_text(text[start:end])
        if where == "svg":
            mark = f'<tspan class="kit-figure-svg" data-kit-source="{label.kind}"> ({html.escape(label.text)})</tspan>'
            out.append(raw + mark)
        else:
            out.append(figure_mark(raw, label))
        at = end
    out.append(render_text(text[at:]))
    return "".join(out)


class Renderer:
    """Pieces to HTML, in order, with the platform's frame. Text and data are rendered with the
    figure marks; markup as the sanitizer rebuilt it."""

    def __init__(self, locale: str, labels: FigureLabels):
        self.locale = locale
        self.labels = labels
        self.count = FigureCount()
        self.started = False
        self.in_body = False
        self.title: str = ""
        self._in_title = False

    def render(self, pieces: Sequence[Piece]) -> str:
        out: list[str] = []
        if not self.started:
            self.started = True
            out.append(prologue(self.locale))
        for piece in pieces:
            if not self.in_body and not self._head_piece(piece):
                self.in_body = True
                out.append("</head><body>")
            out.append(self._piece(piece))
        return "".join(out)

    def _head_piece(self, piece: Piece) -> bool:
        if isinstance(piece, Style):
            return True
        if isinstance(piece, Markup):
            return piece.tag in _HEAD_TAGS
        if isinstance(piece, Text):
            return piece.where == "title" or not piece.text.strip()
        return False

    def _piece(self, piece: Piece) -> str:
        if isinstance(piece, Text):
            if piece.where == "title":
                self.title += piece.text
            if not self.in_body and not piece.text.strip() and piece.where != "title":
                return ""
            return mark_text(piece.text, piece.where, self.labels, self.count)
        if isinstance(piece, Style):
            return render_style(piece.css)
        if isinstance(piece, Data):
            data = mark_data(piece.parsed if piece.parsed is not None else normalize_data(piece.raw), self.labels, self.count)
            return render_data(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
        if piece.attrs is not None and not piece.end:
            return start_tag(piece.tag, piece.attrs) if piece.tag else piece.html
        return piece.html

    def end(self, sources: str) -> str:
        """What closes the page: the body if it never began, the sources section, the frame's end."""
        head = "" if self.in_body else "</head><body>"
        self.in_body = True
        return head + sources + "</body></html>"


def sources_section(citations: Sequence[dict], locale: str, text: ReportText, *, general: bool) -> str:
    """«المصادر» from the turn's citations (I3): KB › title, publisher, date and pages, or a note that the
    figures are the model's own when there is no source. Western digits (ADR-0288)."""
    lang, dir_ = direction(locale)
    items = []
    for c in citations:
        kb = (c.get("kb_name") or {}).get(lang) if isinstance(c.get("kb_name"), dict) else None
        title = f"{kb} › {c.get('title') or ''}" if kb else (c.get("title") or "")
        parts = [html.escape(title)]
        parts += [html.escape(str(c[key])) for key in ("publisher", "published_on") if c.get(key)]
        pages = c.get("pages") or []
        if pages:
            parts.append(html.escape(f"{text.page.of(locale)} {', '.join(str(p) for p in pages)}"))
        elif c.get("locator"):
            parts.append(html.escape(str(c["locator"])))
        items.append("<li>" + " — ".join(parts) + "</li>")
    body = f"<ol>{''.join(items)}</ol>" if items else f'<p class="kit-sources-note">{html.escape(text.no_sources.of(locale))}</p>'
    note = (
        f'<p class="kit-sources-note">{html.escape(text.general_note.of(locale))}</p>' if general and items else ""
    )
    return (
        f'<section class="kit-sources" data-kit="sources" lang="{lang}" dir="{dir_}">'
        f"<h2>{html.escape(text.sources_heading.of(locale))}</h2>{body}{note}</section>"
    )


def served(page: str, kit: Kit, *, form: str, kit_base: str = "") -> str:
    """The stored page as it leaves: `view` links the kit from `kit_base` (the kit path on the page's own
    origin); `download` inlines it, with a CSP of its own, so the file opens offline and loads nothing."""
    if form == "view":
        tags = (
            f'<link rel="stylesheet" href="{html.escape(kit_base + kit.config.stylesheet)}">'
            + "".join(f'<script src="{html.escape(kit_base + path)}" defer></script>' for path, _ in kit.scripts)
        )
    else:
        csp = DOWNLOAD_CSP.format(hashes=" ".join(kit.script_hashes()))
        tags = (
            f'<meta http-equiv="Content-Security-Policy" content="{csp}">'
            f"<style>{kit.inline_stylesheet()}</style>"
            + "".join(f"<script>{source}</script>" for _, source in kit.scripts)
        )
    return page.replace(KIT_MARKER, tags, 1)


_SOURCES = re.compile(r'<section class="kit-sources" data-kit="sources"[^>]*>.*?</section>', re.S)
_MARK = re.compile(r'<span class="kit-figure" data-kit="figure"[^>]*>(.*?)</span>', re.S)
_SVG_MARK = re.compile(r'<tspan class="kit-figure-svg"[^>]*>.*?</tspan>', re.S)


def editable(page: str) -> str:
    """A stored version as a follow-up's model reads it (D4): without the platform's marks and sources
    section, which the platform adds again to the new version."""
    return _SVG_MARK.sub("", _MARK.sub(r"\1", _SOURCES.sub("", page)))
