"""A report page from the sanitized pieces (reports PR, ADR-0380 to ADR-0382).

The platform writes the frame: `<!doctype html>`, `<html lang dir>` from the turn's locale (Arabic:
dir="rtl"), the charset and viewport, the kit marker, then the model's title, styles and body. The
same renderer produces the live deltas and the stored page, so what streams is what is kept, plus
what the platform appends at the end (the sources section). The kit is never stored in the page:
`KIT_MARKER` stands where it goes, and `served` puts the kit's links there for the sandboxed view
or inlines the whole kit for a download that opens offline (I6)."""

import copy
import html
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.artifacts.config import Kit, ModelInfoText, ReportText
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
    # Labelled general knowledge: the figures of an unsourced turn (D2, ADR-0388, ADR-0398).
    general: int = 0
    general_figures: list[str] = field(default_factory=list)
    # Not in a sourced turn's findings: shown as «غير متوفر» / "Not available" (ADR-0398).
    unavailable: int = 0
    # The figures the page shows that this turn's sources hold, with their labels (stored with the version).
    sourced: dict[str, Label] = field(default_factory=dict)

    def add(self, figure: str, label: Label) -> None:
        self.total += 1
        if label.kind == "general":
            self.general += 1
            self.general_figures.append(figure)
        elif label.kind == "unavailable":
            self.unavailable += 1
        else:
            self.sourced.setdefault(figure, label)


def _label(figure: str, labels: FigureLabels, count: FigureCount, context: Sequence[str] = ()) -> Label:
    label = labels.of(figure, context)
    count.add(figure, label)
    return label


def _replaced(text: str, labels: FigureLabels, count: FigureCount, context: Sequence[str]) -> tuple[str, list[str]]:
    """An axis label with each figure no source holds replaced by «غير متوفر», and the labels it got."""
    out, at, found = [], 0, []
    for start, end, figure in spans(text):
        label = _label(figure, labels, count, context)
        found.append(label.text)
        out.append(text[at:start])
        out.append(label.text if label.kind == "unavailable" else text[start:end])
        at = end
    out.append(text[at:])
    return "".join(out), found


def mark_data(data: dict, labels: FigureLabels, count: FigureCount) -> dict:
    """Gives each point, and each axis label that holds a figure, its source label (ADR-0388): the kit
    shows them in the chart's tooltips and in a note under it, and draws general-knowledge points dashed.
    A point is matched with its category, its series and the chart's title as its labels (ADR-0399); on a
    sourced turn a point no source holds is dropped (null) and its label reads «غير متوفر» (ADR-0398)."""
    for chart in data["charts"].values():
        series = [d["label"] for d in chart["datasets"]]
        label_sources: list[str | None] = []
        for i, text in enumerate(chart["labels"]):
            chart["labels"][i], found = _replaced(text, labels, count, [chart["title"], *series])
            label_sources.append(" · ".join(dict.fromkeys(found)) or None)
        if any(label_sources):
            chart["label_sources"] = label_sources
        for dataset in chart["datasets"]:
            sources: list[str | None] = []
            general: list[int] = []
            missing: list[int] = []
            for i, value in enumerate(dataset["data"]):
                if value is None:
                    sources.append(None)
                    continue
                category = chart["labels"][i] if i < len(chart["labels"]) else ""
                label = _label(canonical_number(value), labels, count, [category, dataset["label"], chart["title"]])
                sources.append(label.text)
                if label.kind == "general":
                    general.append(i)
                elif label.kind == "unavailable":
                    dataset["data"][i] = None
                    missing.append(i)
            dataset["sources"] = sources
            if general:
                dataset["general"] = general
            if missing:
                dataset["unavailable"] = missing
    return data


def figure_mark(raw: str, label: Label) -> str:
    """A figure in its label's marker: the label reads after it (kit.css), and on hover."""
    text = html.escape(label.text)
    return (
        f'<span class="kit-figure" data-kit="figure" data-kit-source="{label.kind}" '
        f'data-kit-label="{text}" title="{text}">{raw}</span>'
    )


def unavailable_mark(label: Label) -> str:
    """A figure the turn's findings do not hold: «غير متوفر» in its place, no label after it (ADR-0398)."""
    text = html.escape(label.text)
    return f'<span class="kit-figure" data-kit="figure" data-kit-source="unavailable" title="{text}">{text}</span>'


_PERCENT = "%٪"


def mark_text(text: str, where: str, labels: FigureLabels, count: FigureCount, context: Sequence[str] = ()) -> str:
    """The text, escaped, with each figure in its source label's marker (ADR-0388). Inside an SVG
    drawing, where no span can wrap it, the figure is followed by its label's words. `context` is the
    figure's row and column labels; the text itself is always part of it (ADR-0399)."""
    if where == "title":
        return render_text(text)
    context = [text, *context]
    out, at = [], 0
    for start, end, figure in spans(text):
        if start < at:
            continue
        label = _label(figure, labels, count, context)
        out.append(render_text(text[at:start]))
        if label.kind == "unavailable":
            # The number and its percent sign give way to «غير متوفر».
            if end < len(text) and text[end] in _PERCENT:
                end += 1
            out.append(html.escape(label.text) if where == "svg" else unavailable_mark(label))
            at = end
            continue
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
        # The row and column labels of a figure (ADR-0399): the text before it, and inside a table the
        # row's first cell and the column's header.
        self._before = ""
        self._headers: list[str] = []
        self._row: list[str] = []
        self._row_heads = True
        self._cell = -1
        self._in_cell = False
        # Owner decision 3 on PR #17 (ADR-0405): per table, in order, [figures in its cells, of them not
        # available]; the charts whose every point is not available.
        self.tables: list[list[int]] = []
        self._open_tables: list[int] = []
        self.empty_charts: set[str] = set()

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

    def _context(self) -> list[str]:
        if self._in_cell:
            column = self._headers[self._cell] if 0 <= self._cell < len(self._headers) else ""
            row = self._row[0] if self._cell > 0 and self._row else ""
            return [column, row]
        return [self._before]

    def _table(self, piece: Markup) -> None:
        tag = piece.tag
        if tag == "table":
            if piece.end:
                if self._open_tables:
                    self._open_tables.pop()
            else:
                self.tables.append([0, 0])
                self._open_tables.append(len(self.tables) - 1)
        if tag == "table" and not piece.end:
            self._headers, self._row, self._cell = [], [], -1
        elif tag == "tr":
            if piece.end:
                if self._row_heads and self._row:
                    self._headers = list(self._row)
                self._in_cell = False
            else:
                self._row, self._row_heads, self._cell = [], True, -1
        elif tag in ("td", "th"):
            if piece.end:
                self._in_cell = False
            else:
                self._cell += 1
                self._in_cell = True
                self._row.append("")
                self._row_heads = self._row_heads and tag == "th"

    def _piece(self, piece: Piece) -> str:
        if isinstance(piece, Text):
            if piece.where == "title":
                self.title += piece.text
            if not self.in_body and not piece.text.strip() and piece.where != "title":
                return ""
            total, missing = self.count.total, self.count.unavailable
            out = mark_text(piece.text, piece.where, self.labels, self.count, self._context())
            if self._in_cell and self._open_tables:
                table = self.tables[self._open_tables[-1]]
                table[0] += self.count.total - total
                table[1] += self.count.unavailable - missing
            if piece.text.strip():
                if self._in_cell and self._row:
                    self._row[-1] += piece.text.strip()
                if any(c.isalpha() for c in piece.text):
                    self._before = piece.text.strip()  # a label has words; a unit or a lone figure is not one
            return out
        if isinstance(piece, Markup) and piece.tag:
            self._table(piece)
        if isinstance(piece, Style):
            return render_style(piece.css)
        if isinstance(piece, Data):
            # A copy: marking drops the points no source holds, and the page is rendered twice (stream, store).
            data = mark_data(copy.deepcopy(piece.parsed) if piece.parsed is not None else normalize_data(piece.raw), self.labels, self.count)
            for chart_id, chart in data["charts"].items():
                points = [v for d in chart["datasets"] for v in d["data"]]
                if self.labels.sourced and points and all(v is None for v in points):
                    self.empty_charts.add(chart_id)
            return render_data(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
        if piece.attrs is not None and not piece.end:
            return start_tag(piece.tag, piece.attrs) if piece.tag else piece.html
        return piece.html

    def end(self, sources: str) -> str:
        """What closes the page: the body if it never began, the sources section, the frame's end."""
        head = "" if self.in_body else "</head><body>"
        self.in_body = True
        return head + sources + "</body></html>"


def drop_empty(pieces: Sequence[Piece], probe: Renderer) -> list[Piece]:
    """Owner decision 3 on PR #17 (ADR-0405): a chart whose every point, or a table whose every figure, is
    «غير متوفر» leaves the page, with the kit card around it when there is one; a chart or table with some
    gaps stays. `probe` rendered the same pieces once and found them. The chart's entry leaves report-data."""
    empty_tables = {i for i, (total, missing) in enumerate(probe.tables) if total and total == missing}
    if not empty_tables and not probe.empty_charts:
        return list(pieces)
    # Each start tag's end and parent, by index.
    ends: dict[int, int] = {}
    parent: dict[int, int | None] = {}
    stack: list[int] = []
    for i, piece in enumerate(pieces):
        if not isinstance(piece, Markup) or not piece.tag or piece.void:
            continue
        if not piece.end:
            parent[i] = stack[-1] if stack else None
            stack.append(i)
        elif any(pieces[j].tag == piece.tag for j in stack):  # type: ignore[union-attr]
            while stack:
                j = stack.pop()
                ends[j] = i
                if pieces[j].tag == piece.tag:  # type: ignore[union-attr]
                    break
    for j in stack:
        ends[j] = len(pieces) - 1

    def card(i: int) -> int:
        """The nearest enclosing kit card that holds nothing else of the kind, else the element itself."""
        at = parent.get(i)
        while at is not None:
            classes = dict(pieces[at].attrs).get("class", "")  # type: ignore[union-attr]
            if "kit-card" in classes.split():
                inside = range(at + 1, ends.get(at, at))
                others = [k for k in inside if k != i and isinstance(pieces[k], Markup) and not pieces[k].end
                          and (pieces[k].tag == "table" or dict(pieces[k].attrs).get("data-chart"))]  # type: ignore[union-attr]
                return at if not any(not (i < k <= ends.get(i, i)) for k in others) else i
            at = parent.get(at)
        return i

    cut: list[tuple[int, int]] = []
    table = -1
    for i, piece in enumerate(pieces):
        if not isinstance(piece, Markup) or piece.end or not piece.tag:
            continue
        if piece.tag == "table":
            table += 1
            if table in empty_tables:
                start = card(i)
                cut.append((start, ends.get(start, start)))
        elif dict(piece.attrs).get("data-chart") in probe.empty_charts:
            start = card(i)
            cut.append((start, ends.get(start, start)))
    dropped = {k for start, end in cut for k in range(start, end + 1)}
    kept: list[Piece] = []
    for k, piece in enumerate(pieces):
        if k in dropped:
            continue
        if isinstance(piece, Data) and probe.empty_charts:
            parsed = copy.deepcopy(piece.parsed if piece.parsed is not None else normalize_data(piece.raw))
            parsed["charts"] = {c: v for c, v in parsed["charts"].items() if c not in probe.empty_charts}
            piece = Data(piece.raw, parsed)
        kept.append(piece)
    return kept


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


@dataclass(frozen=True)
class ModelCall:
    """One model call behind a report, as its model strip shows it (owner decision C, ADR-0391)."""

    label: str  # what it did: wrote the report, or produced a capability's findings
    served_model: str | None  # LiteLLM's name for the deployment, "<provider>/<model id>"
    alias: str
    duration_s: float
    tokens_in: int
    tokens_out: int

    @property
    def provider_and_model(self) -> tuple[str | None, str | None]:
        if not self.served_model:
            return None, None
        provider, _, model = self.served_model.partition("/")
        return (provider, model) if model else (None, provider)


def model_strip(calls: Sequence[ModelCall], locale: str, text: ModelInfoText) -> str:
    """The strip at the end of a development report: one row per call, platform-written, never labelled."""
    lang, dir_ = direction(locale)
    unknown = text.unknown.of(locale)
    head = "".join(f"<th>{html.escape(t.of(locale))}</th>" for t in (text.call, text.model, text.provider, text.tier, text.time, text.tokens))
    rows = []
    for call in calls:
        provider, model = call.provider_and_model
        cells = (
            call.label,
            model or unknown,
            provider or unknown,
            call.alias or unknown,
            text.seconds.of(locale).format(value=f"{call.duration_s:.1f}"),
            f"{call.tokens_in:,} / {call.tokens_out:,}",
        )
        rows.append("<tr>" + "".join(f"<td><bdi>{html.escape(c)}</bdi></td>" for c in cells) + "</tr>")
    return (
        f'<footer class="kit-model-info" data-kit="model-info" lang="{lang}" dir="{dir_}">'
        f"<p>{html.escape(text.heading.of(locale))}</p>"
        f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table></footer>"
    )


_MODEL_INFO = re.compile(r'<footer class="kit-model-info" data-kit="model-info"[^>]*>.*?</footer>', re.S)


def without_model_info(page: str) -> str:
    """A stored page with its model strip removed: how it leaves when the setting is false (ADR-0391)."""
    return _MODEL_INFO.sub("", page)


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
    return _SVG_MARK.sub("", _MARK.sub(r"\1", _SOURCES.sub("", without_model_info(page))))
