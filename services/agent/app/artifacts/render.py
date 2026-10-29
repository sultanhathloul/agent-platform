"""A checked report spec rendered to the stored HTML page, by the platform, deterministically (owner
decision A2 on PR #17, ADR-0406): the same spec always gives the same bytes, so the panel, the stored version
and the download are one file. The model writes no markup: every string is escaped here, Markdown is a small
safe subset (paragraphs, lists, bold, italic, line breaks), and each chart's data travels as JSON in a
`<script type="application/json" class="kit-chart-data">` the kit reads with ECharts (vendored, pinned).

No label beside any figure (owner decision B, ADR-0408): a value the check did not find reads «—»; the page
ends with «المصادر» / "Sources", one entry per document and under it the elements it supports with their
pages or sheets, and the values each could not find; a general-knowledge report opens with one line saying
so and has no sources section."""

import html
import json
import re
from collections.abc import Sequence

from app.artifacts.config import ReportText
from app.artifacts.document import direction, json_block, prologue
from app.artifacts.spec import Callout, Chart, Head, KpiRow, ReportSpec, Table, Text
from app.artifacts.verify import MISSING, Document

e = html.escape


def _number(value) -> str:
    if value is None:
        return MISSING
    if isinstance(value, int):
        return f"{value:,}"
    if value.is_integer():
        return f"{value:,.1f}"  # 3.0 as the source writes it
    return f"{value:,.2f}".rstrip("0").rstrip(".")


_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])")


def _inline(text: str) -> str:
    return _ITALIC.sub(r"<em>\1</em>", _BOLD.sub(r"<strong>\1</strong>", e(text)))


def markdown(text: str) -> str:
    """Paragraphs, bullet and numbered lists, **bold**, *italic*; nothing else becomes markup."""
    out: list[str] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [line.rstrip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        if all(re.match(r"^\s*[-*•]\s+", line) for line in lines):
            items = "".join(f"<li>{_inline(re.sub(r'^\s*[-*•]\s+', '', line))}</li>" for line in lines)
            out.append(f"<ul>{items}</ul>")
        elif all(re.match(r"^\s*\d+[.)]\s+", line) for line in lines):
            items = "".join(f"<li>{_inline(re.sub(r'^\s*\d+[.)]\s+', '', line))}</li>" for line in lines)
            out.append(f"<ol>{items}</ol>")
        else:
            body = "<br>".join(_inline(re.sub(r"^#+\s*", "", line)) for line in lines)
            out.append(f"<p>{body}</p>")
    return "".join(out)


def kpi_columns(count: int) -> int:
    """Columns for a row of KPI cards so the last row is not one card alone when that can be avoided
    (ADR-0416): up to 4 in one row; 5, 6 and 9 in rows of 3; 7 and 8 in rows of 4."""
    if count <= 4:
        return max(count, 1)
    return 3 if count in (5, 6, 9) else 4


class PageRenderer:
    """The page in parts: `start` (the frame and the header), one `section` each, then `end`."""

    def __init__(self, locale: str, text: ReportText, *, sourced: bool):
        self.locale = locale
        self.text = text
        self.sourced = sourced
        self._charts = 0

    def start(self, head: Head) -> str:
        lang, dir_ = direction(self.locale)
        parts = [prologue(self.locale), f"<title>{e(head.title)}</title></head><body>", '<main class="kit-page">']
        parts.append(f'<header class="kit-header"><h1>{e(head.title)}</h1>')
        if head.subtitle:
            parts.append(f'<p class="kit-card-subtitle">{e(head.subtitle)}</p>')
        if head.period:
            parts.append(f'<p class="kit-meta">{e(head.period)}</p>')
        parts.append("</header>")
        if not self.sourced:
            parts.append(f'<p class="kit-general-note" data-kit="general">{e(self.text.general_banner.of(self.locale))}</p>')
        return "".join(parts)

    def section(self, section) -> str:
        if isinstance(section, KpiRow):
            return self._kpis(section)
        if isinstance(section, Chart):
            return self._chart(section)
        if isinstance(section, Table):
            return self._table(section)
        if isinstance(section, Callout):
            tone = " kit-callout-warning" if section.tone == "warning" else ""
            title = f"<strong>{e(section.title)}</strong>" if section.title else ""
            return f'<section class="kit-section"><div class="kit-callout{tone}">{title}{markdown(section.markdown)}</div></section>'
        if isinstance(section, Text):
            title = f"<h2>{e(section.title)}</h2>" if section.title else ""
            return f'<section class="kit-section">{title}{markdown(section.markdown)}</section>'
        return ""

    def _kpis(self, row: KpiRow) -> str:
        title = f"<h2>{e(row.title)}</h2>" if row.title else ""
        tiles = []
        for item in row.items:
            unit = f'<span class="kit-kpi-unit">{e(item.unit)}</span>' if item.unit and item.value is not None else ""
            value_class = "kit-kpi-value" + (" kit-missing" if item.value is None else "")
            delta = ""
            if item.delta is not None:
                sign = "kit-up" if item.delta > 0 else "kit-down"
                delta = f'<div class="kit-kpi-delta {sign}">{"+" if item.delta > 0 else ""}{_number(item.delta)}</div>'
            tiles.append(
                f'<div class="kit-kpi"><div class="kit-kpi-label">{e(item.label)}</div>'
                f'<div class="{value_class}">{_number(item.value)}{unit}</div>{delta}</div>'
            )
        cols = kpi_columns(len(row.items))
        return f'<section class="kit-section">{title}<div class="kit-kpis kit-kpis-{cols}">{"".join(tiles)}</div></section>'

    def _chart(self, chart: Chart) -> str:
        self._charts += 1
        chart_id = f"c{self._charts}"
        data = {
            "type": chart.type,
            "title": chart.title,
            "labels": chart.labels,
            "unit": chart.unit,
            "series": [{"name": s.name, "data": s.data} for s in chart.series],
        }
        missing = sum(1 for s in chart.series for v in s.data if v is None)
        note = (
            f'<p class="kit-chart-note">{e(self.text.chart_gaps.of(self.locale).format(count=missing))}</p>' if missing else ""
        )
        return (
            f'<section class="kit-section"><div class="kit-card"><h2>{e(chart.title)}</h2>'
            f'<div class="kit-chart" data-chart="{chart_id}"></div>{note}'
            f'{json_block(json.dumps(data, ensure_ascii=False, separators=(",", ":")), chart_id)}</div></section>'
        )

    def _table(self, table: Table) -> str:
        head = "".join(f"<th>{e(c)}</th>" for c in table.columns)
        rows = []
        for row in table.rows:
            cells = []
            for c, cell in enumerate(row):
                if isinstance(cell, int | float) and not isinstance(cell, bool):
                    cells.append(f'<td class="kit-num">{_number(cell)}</td>')
                elif cell == MISSING:
                    cells.append(f'<td class="kit-num kit-missing">{MISSING}</td>')
                else:
                    tag = "th" if c == 0 else "td"
                    cells.append(f"<{tag}>{e(str(cell)) if cell is not None else ''}</{tag}>")
            rows.append(f"<tr>{''.join(cells)}</tr>")
        return (
            f'<section class="kit-section"><div class="kit-card"><h2>{e(table.title)}</h2><div class="kit-table-wrap">'
            f'<table class="kit-table" data-kit-sortable><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>'
            "</div></div></section>"
        )

    def sources(self, documents: Sequence[Document]) -> str:
        """«المصادر»: one entry per document, under it each element it supports, with pages or sheets and the
        values it could not find (ADR-0408). None on a general-knowledge report."""
        if not self.sourced:
            return ""
        text, locale = self.text, self.locale
        lang, dir_ = direction(locale)
        entries = []
        for doc in documents:
            kb = (doc.kb_name or {}).get(lang) if isinstance(doc.kb_name, dict) else None
            head = [f"{kb} › {doc.title}" if kb else doc.title]
            head += [str(v) for v in (doc.publisher, doc.published_on) if v]
            uses = []
            for use in doc.uses:
                where = [
                    f"{text.page.of(locale)} {w[1:]}" if w.startswith("p") else f"{text.sheet.of(locale)} {w[1:]}"
                    for w in use.where
                ]
                line = f"{use.kind}: {use.title}" + (f" — {'، '.join(where) if lang == 'ar' else ', '.join(where)}" if where else "")
                missing = ""
                if use.unavailable:
                    joined = ("، " if lang == "ar" else ", ").join(use.unavailable[:12])
                    missing = f'<div class="kit-sources-missing">{e(text.unavailable.of(locale).format(values=joined))}</div>'
                uses.append(f"<li>{e(line)}{missing}</li>")
            entries.append(f'<li><div class="kit-source-doc">{e(" — ".join(head))}</div><ul>{"".join(uses)}</ul></li>')
        body = f"<ol>{''.join(entries)}</ol>" if entries else f'<p class="kit-sources-note">{e(text.no_sources.of(locale))}</p>'
        return (
            f'<section class="kit-sources" data-kit="sources" lang="{lang}" dir="{dir_}">'
            f"<h2>{e(text.sources_heading.of(locale))}</h2>{body}</section>"
        )

    def end(self, spec_json: str, tail: str) -> str:
        """The checked spec (for the next version and the evals), the sources, the strip and the frame's end."""
        return f"{tail}{json_block(spec_json, 'report-spec')}</main></body></html>"


def render_page(spec: ReportSpec, renderer: PageRenderer, sources: str, strip: str) -> str:
    head = Head(title=spec.title, subtitle=spec.subtitle, period=spec.period, language=spec.language)
    body = "".join(renderer.section(s) for s in spec.sections)
    spec_json = spec.model_dump_json(exclude_none=True)
    return renderer.start(head) + body + renderer.end(spec_json, sources + strip)
