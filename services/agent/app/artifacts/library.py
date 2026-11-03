"""The report library's text forms (report library PR, owner decisions 1 to 3; ADR-0430 to ADR-0432).

A stored version's checked spec (ADR-0406) is the one source of all three:

- `digest`: the compact form every turn's context lists for the conversation's reports (decision 1), and the
  history marker of a report reply carries (the window): title, version, section titles, KPI labels and
  values, table headers and chart titles. Never the page, so the model has nothing to imitate.
- `readable`: what `report_read` returns (decision 2): every section's values, a table's rows, a chart's
  points, the text and the sources, cut to config/library.yaml read.max_chars.
- `index_text`: what agent.report_library indexes (decision 3): the title, the subject (subtitle and period),
  the section titles, KPI labels and values, table headers, chart titles and the sources' names,
  normalized for full-text search like a chunk's `text_norm` (app/text/arabic.py).

All three are linear in the spec, which is itself bounded by its schema (app/artifacts/spec.py) and the
version's size cap (I4); each output is cut to its configured size (CLAUDE.md rule 12d)."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.text.arabic import normalize_for_search

INDEX_TEXT_MAX = 20000  # agent.report_library.text_norm's CHECK


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Words(_Strict):
    ar: list[str] = []
    en: list[str] = []

    def all(self) -> list[str]:
        return [*self.ar, *self.en]


class IndexConfig(_Strict):
    max_reports: int = Field(ge=1, le=50)
    digest_chars: int = Field(ge=100, le=5000)
    marker_chars: int = Field(ge=100, le=5000)


class ReadConfig(_Strict):
    max_chars: int = Field(ge=1000, le=100000)
    max_table_rows: int = Field(ge=1, le=100)


class SearchConfig(_Strict):
    max_results: int = Field(ge=1, le=20)
    max_query_terms: int = Field(ge=1, le=64)
    stopwords: Words = Words()
    page_size: int = Field(ge=1, le=100)


class RecallConfig(_Strict):
    max_messages: int = Field(ge=1, le=100000)
    max_scan_chars: int = Field(ge=1000, le=20_000_000)
    max_results: int = Field(ge=1, le=20)
    message_chars: int = Field(ge=200, le=50000)
    max_chars: int = Field(ge=500, le=100000)
    skip_recent: int = Field(ge=0, le=100)
    table_words: list[str] = []
    list_words: list[str] = []
    early_words: list[str] = []
    stopwords: Words = Words()


class HistoryConfig(_Strict):
    keep_checkpoints: int = Field(ge=20, le=10000)
    prune_s: float = Field(gt=0, le=30)
    page_messages: int = Field(default=400, ge=20, le=5000)


class LibraryConfig(_Strict):
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    index: IndexConfig
    read: ReadConfig
    search: SearchConfig
    recall: RecallConfig
    history: HistoryConfig


def load_library_config(path: Path) -> LibraryConfig:
    return LibraryConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


# ------------------------------------------------------------------ helpers


def _num(value: Any) -> str:
    """A spec value as a person reads it: an integer without a decimal point, no float noise."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:.6g}"
    return str(value)


def _value(item: dict) -> str:
    unit = item.get("unit") or ""
    text = _num(item.get("value"))
    if unit and item.get("value") is not None:
        text = f"{text}{unit}" if unit in ("%", "٪") else f"{text} {unit}"
    if item.get("delta") is not None:
        text += f" (Δ {_num(item.get('delta'))})"
    return text


def _cut(text: str, limit: int, mark: str = " …") -> str:
    return text if len(text) <= limit else text[: max(limit - len(mark), 0)].rstrip() + mark


def _sections(spec: dict) -> list[dict]:
    return [s for s in spec.get("sections") or [] if isinstance(s, dict)]


_KIND_LABEL = {
    "ar": {"kpi_row": "مؤشرات", "chart": "رسم", "table": "جدول", "text": "نص", "callout": "ملاحظة"},
    "en": {"kpi_row": "Indicators", "chart": "Chart", "table": "Table", "text": "Text", "callout": "Note"},
}


def _label(kind: str, locale: str) -> str:
    return _KIND_LABEL["en" if locale == "en" else "ar"].get(kind, kind)


# ------------------------------------------------------------------ digest (decision 1)


@dataclass(frozen=True)
class Digest:
    report_id: str
    title: str
    version: int
    lines: list[str] = field(default_factory=list)

    def text(self, limit: int) -> str:
        return _cut("\n".join(self.lines), limit)


def digest(report_id: str, title: str, version: int, spec: dict | None, locale: str) -> Digest:
    """Section titles, KPI labels and values, table headers and chart titles, one line per section."""
    lines: list[str] = []
    spec = spec or {}
    head = [str(spec.get(k)) for k in ("subtitle", "period") if spec.get(k)]
    if head:
        lines.append(" · ".join(head))
    for section in _sections(spec):
        kind = str(section.get("kind") or "")
        name = _label(kind, locale)
        heading = str(section.get("title") or "").strip()
        if kind == "kpi_row":
            values = "؛ ".join(
                f"{i.get('label')}: {_value(i)}" for i in section.get("items") or [] if isinstance(i, dict)
            )
            lines.append(f"- {name}{(' «' + heading + '»') if heading else ''}: {values}")
        elif kind == "table":
            columns = " | ".join(str(c) for c in section.get("columns") or [])
            rows = len(section.get("rows") or [])
            lines.append(f"- {name} «{heading}» ({columns}; {rows})")
        elif kind == "chart":
            labels = section.get("labels") or []
            series = ", ".join(str(s.get("name")) for s in section.get("series") or [] if isinstance(s, dict))
            lines.append(f"- {name} «{heading}» ({series}; {len(labels)})")
        elif heading:
            lines.append(f"- {name} «{heading}»")
    return Digest(report_id=report_id, title=title, version=version, lines=lines)


# ------------------------------------------------------------------ readable text (decision 2)


def _source_line(citation: dict, locale: str) -> str:
    kb = citation.get("kb_name") if isinstance(citation.get("kb_name"), dict) else {}
    kb_name = (kb.get("en") if locale == "en" else kb.get("ar")) or ""
    parts = [p for p in (kb_name, citation.get("title"), citation.get("publisher"), citation.get("published_on")) if p]
    pages = citation.get("pages") or []
    if pages:
        parts.append(("p. " if locale == "en" else "ص ") + ", ".join(str(p) for p in pages[:20]))
    return "- " + " — ".join(str(p) for p in parts)


def readable(
    *, title: str, version: int, spec: dict | None, citations: list[dict], locale: str, config: ReadConfig,
    general: bool = False,
) -> str:
    """The version as text a model can answer from: every value of every section, cut to `max_chars`."""
    spec = spec or {}
    en = locale == "en"
    out: list[str] = [f"# {title} ({'version' if en else 'الإصدار'} {version})"]
    for key in ("subtitle", "period"):
        if spec.get(key):
            out.append(str(spec[key]))
    if general:
        out.append("(general knowledge, no verified sources)" if en else "(من المعرفة العامة، دون مصادر موثقة)")
    for section in _sections(spec):
        kind = str(section.get("kind") or "")
        heading = str(section.get("title") or "").strip()
        out.append("")
        out.append(f"## {_label(kind, locale)}{(': ' + heading) if heading else ''}")
        if kind == "kpi_row":
            for item in section.get("items") or []:
                if isinstance(item, dict):
                    out.append(f"- {item.get('label')}: {_value(item)}")
        elif kind == "chart":
            labels = [str(label) for label in section.get("labels") or []]
            unit = section.get("unit") or ""
            out.append(f"({section.get('type')}{(', ' + unit) if unit else ''})")
            for series in section.get("series") or []:
                if not isinstance(series, dict):
                    continue
                data = series.get("data") or []
                points = "، ".join(
                    f"{labels[i] if i < len(labels) else i + 1}: {_num(v)}" for i, v in enumerate(data)
                )
                out.append(f"- {series.get('name')}: {points}")
        elif kind == "table":
            columns = [str(c) for c in section.get("columns") or []]
            out.append("| " + " | ".join(columns) + " |")
            rows = section.get("rows") or []
            for row in rows[: config.max_table_rows]:
                out.append("| " + " | ".join(_num(c) if not isinstance(c, str) else c for c in row) + " |")
            if len(rows) > config.max_table_rows:
                out.append(f"(+{len(rows) - config.max_table_rows})")
        elif kind in ("text", "callout"):
            out.append(str(section.get("markdown") or ""))
    if citations:
        out.append("")
        out.append("## Sources" if en else "## المصادر")
        out.extend(_source_line(c, locale) for c in citations if isinstance(c, dict))
    marker = "\n[… cut]" if en else "\n[… اقتُطع]"
    return _cut("\n".join(out), config.max_chars, marker)


# ------------------------------------------------------------------ index (decision 3)


def source_names(citations: list[dict]) -> list[str]:
    names: list[str] = []
    for citation in citations:
        if not isinstance(citation, dict):
            continue
        kb = citation.get("kb_name") if isinstance(citation.get("kb_name"), dict) else {}
        for name in (kb.get("ar"), kb.get("en"), citation.get("title"), citation.get("publisher")):
            if isinstance(name, str) and name and name not in names:
                names.append(name)
    return names[:40]


def source_kinds(citations: list[dict], general: int) -> list[str]:
    """`kb:<slug>` per cited knowledge base, `file` for an attached file, `general` for a general-knowledge
    version (no citation): the page's source filter."""
    kinds: list[str] = []
    for citation in citations:
        if not isinstance(citation, dict):
            continue
        kind = citation.get("source") or "kb"
        tag = f"kb:{citation.get('kb')}" if kind == "kb" and citation.get("kb") else ("file" if kind == "doc" else None)
        if tag and tag not in kinds:
            kinds.append(tag)
    if not kinds:
        kinds.append("general")
    return kinds


def subject(spec: dict | None) -> str:
    spec = spec or {}
    return " · ".join(str(spec.get(k)) for k in ("subtitle", "period") if spec.get(k))[:400]


def section_titles(spec: dict | None) -> list[dict]:
    return [
        {"kind": str(s.get("kind") or ""), "title": str(s.get("title") or "")[:200]}
        for s in _sections(spec or {})
    ][:30]


def index_text(title: str, spec: dict | None, citations: list[dict]) -> str:
    """The searchable text of a version, normalized for full-text search and capped."""
    spec = spec or {}
    parts: list[str] = [title, subject(spec)]
    for section in _sections(spec):
        parts.append(str(section.get("title") or ""))
        if section.get("kind") == "kpi_row":
            parts += [f"{i.get('label')} {_value(i)}" for i in section.get("items") or [] if isinstance(i, dict)]
        elif section.get("kind") == "table":
            parts += [str(c) for c in section.get("columns") or []]
            parts += [str(row[0]) for row in (section.get("rows") or [])[:30] if row and isinstance(row[0], str)]
        elif section.get("kind") == "chart":
            parts += [str(s.get("name")) for s in section.get("series") or [] if isinstance(s, dict)]
            parts += [str(label) for label in (section.get("labels") or [])[:40]]
    parts += source_names(citations)
    return normalize_for_search(" ".join(p for p in parts if p))[:INDEX_TEXT_MAX]
