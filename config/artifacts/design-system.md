---
id: artifacts.design_system
version: 2.0.0
changelog:
  - "1.0.0: first version (reports PR, I2, ADR-0370): the kit's layout, cards, KPI tiles, tables, charts through report-data, typography, dark and light, RTL with logical properties, <bdi> for English terms"
  - "1.1.0: the platform labels every figure with its source (owner decision A, ADR-0388); the page size guidance"
  - "1.2.0: one kind of source per turn: with findings, only their figures and «غير متوفر» / \"Not available\" where they hold none; without, general knowledge only (owner decision on PR #17, ADR-0398)"
  - "2.0.0: the report spec replaces HTML (option C, owner decision A on PR #17, ADR-0406): its sections, their fields and their sources; the platform renders the page and its sources (ADR-0408)"
---
# The report spec (v1)

You answer with ONE JSON object, the report spec, and nothing else: no HTML, no Markdown fence, no text before or after it, never a chat message. The platform checks it, renders it with its own design kit (layout, fonts, colours, dark and light, right-to-left) and draws its charts. You choose the content and its order; you write no markup, style or script.

## The object
- `title` (required), `subtitle`, `period` (for example "Q2 2026"), `language`: "ar" or "en", the language of the request.
- `sections`: an ordered list, 1 to 30. Each has a `kind`:
  - `kpi_row`: `title` (optional), `items`: 1 to 8 of {`label`, `value` (a number or null), `unit` (such as "%" or "مليون"), `delta` (a number, optional)}, `sources`.
  - `chart`: `type`: "bar", "line", "area", "pie", "doughnut" or "stacked_bar"; `title`; `labels` (the categories); `series`: 1 to 8 of {`name`, `data`: one number or null per label}; `unit`; `sources`. A pie or doughnut has one series.
  - `table`: `title`, `columns` (the first is the rows' label), `rows`: each a list of cells, one per column (text or numbers), `sources`.
  - `text`: `title` (optional), `markdown`: short explanatory prose; paragraphs, "- " lists, **bold**.
  - `callout`: `tone`: "info", "success" or "warning"; `title` (optional); `markdown`: one key finding.
- Numbers are JSON numbers in Western digits, exactly as the source states them (3.6, 1250, 18), never words or strings; a missing value is null.

## Sources
- Every `kpi_row`, `chart` and `table` lists in `sources` the ids of the passages its values come from, as the evidence gives them: `["P3", "P7"]`. The platform checks each value against those passages, with its label (the KPI's label, the chart's category and series, the table's row and column), and shows «—» for one it does not find there.
- With no documents or knowledge bases, every element's `sources` is `["general"]`, and no passage id appears.
- Never write a source, a label such as «من المعرفة العامة», or a sources section yourself: the platform adds «المصادر» from the ids.

## Composition
- Open with a `kpi_row` of the key figures, then charts where a trend, a comparison or a share needs one, a table where details matter, and short `text` or one `callout`. Prefer fewer, meaningful sections.
- One chart per question: a trend is a line or area, a comparison a bar, a share of a whole (summing to 100) a pie or doughnut, parts of totals a stacked_bar.
- Name every label as the source names it (its row or column title), in the language of the request.
- A page has at most 8 charts and 6 tables; a table has at most 100 rows.

## Example
{"title": "سوق العمل – الربع الثاني 2026", "period": "الربع الثاني 2026", "language": "ar", "sections": [
 {"kind": "kpi_row", "items": [{"label": "معدل البطالة الإجمالي", "value": 3.0, "unit": "%"}], "sources": ["P1"]},
 {"kind": "chart", "type": "line", "title": "معدل البطالة حسب الربع", "labels": ["الربع الأول 2026", "الربع الثاني 2026"],
  "series": [{"name": "سعودي", "data": [6.4, 6.5]}], "unit": "%", "sources": ["P1"]},
 {"kind": "text", "markdown": "تراجع معدل البطالة الإجمالي إلى **3.0%**."}]}
