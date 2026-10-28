---
id: artifacts.design_system
version: 1.0.0
changelog:
  - "1.0.0: first version (reports PR, I2, ADR-0370): the kit's layout, cards, KPI tiles, tables, charts through report-data, typography, dark and light, RTL with logical properties, <bdi> for English terms"
---
# The report design kit (v1)

You write ONE complete HTML document that the platform renders with its own design kit. The kit's stylesheet, fonts (IBM Plex Sans Arabic) and chart library are added by the platform; never link, import or embed any other file, font, image URL or script.

## Document
- Start with `<!doctype html>` and write `<html lang="ar" dir="rtl">` for an Arabic report, `<html lang="en" dir="ltr">` for an English one.
- `<head>`: `<meta charset="utf-8">`, `<meta name="viewport" content="width=device-width, initial-scale=1">` and a short `<title>` that names the report. Nothing else is needed.
- `<body>`: one `<main class="kit-page">` holding a `<header class="kit-header">` and then `<section class="kit-section">` blocks, each with an `<h2>`.
- Keep the page focused: about 6 to 12 components, and the whole document under about 30 KB of HTML. The page is written in one pass within a time limit; a longer page may not finish.
- Write figures in Western digits (0–9), as digits, never as words: 3.6, 1,250, 18%.
- Put an English term inside Arabic text in `<bdi>`: `<bdi>GDP</bdi>`, `<bdi>Vision 2030</bdi>`.
- No `<script>` except the one data block below, no `on…` attributes, no forms, no iframes, no links to other pages (in-page `#anchors` only), no images except inline `<svg>` drawings.

## Layout
- `.kit-header`: `<div class="kit-eyebrow">` (a short label), `<h1>`, `<p class="kit-subtitle">`, `<div class="kit-meta">` (dates, scope) .
- `.kit-grid`: a 12-column grid; children take `.kit-span-3`, `.kit-span-4`, `.kit-span-6`, `.kit-span-8` or the full width. `.kit-row`: a wrapping row of equal items.
- `.kit-card` (optionally `.kit-card-accent`): a panel with `<h3>` and `<p class="kit-card-subtitle">`.

## Components
- KPI tiles: `<div class="kit-kpis">` of `<div class="kit-kpi">` with `<div class="kit-kpi-label">`, `<div class="kit-kpi-value">3.6<span class="kit-kpi-unit">%</span></div>`, optional `<div class="kit-kpi-delta kit-up">+0.5</div>` (or `kit-down`) and `<div class="kit-kpi-note">`.
- A headline number: `<div class="kit-card kit-hero"><div class="kit-hero-value">…</div><div class="kit-hero-label">…</div></div>`.
- Tables: `<div class="kit-table-wrap"><table class="kit-table" data-kit-sortable>` with `<thead>` and `<tbody>`; numeric cells `class="kit-num"`.
- Badges: `<span class="kit-badge kit-badge-good|kit-badge-warning|kit-badge-serious|kit-badge-critical">` with a word (status is never colour alone).
- Callouts: `<div class="kit-callout">` (or `kit-callout-warning`) for a key finding.
- Progress: `<div class="kit-progress"><div class="kit-progress-bar" style="inline-size: 64%"></div></div>` next to its number.
- Lists: `<ul class="kit-list">` of `<li><span>label</span><strong>value</strong></li>`.
- Timeline: `<ol class="kit-timeline">` of `<li class="kit-timeline-item"><div class="kit-timeline-date">…</div>…</li>`.
- Tabs: `<div class="kit-tabs">` of `<div class="kit-tab" data-label="…">` panels; the kit draws the tab bar.
- Presentation style: `<main class="kit-page kit-slides">` of `<section class="kit-slide">`, one idea per slide.
- Infographic: `.kit-stat` (`<span class="kit-stat-value">` + text), `.kit-icon` (an emoji or a small inline `<svg>`), and inline `<svg>` drawings that use `currentColor` or `var(--kit-series-1)` … `var(--kit-series-8)`.

## Charts
Every chart is an empty `<div class="kit-chart" data-chart="ID"></div>` (`kit-chart-tall` or `kit-chart-short` for height) inside a card, and its data lives in the ONE block at the end of `<body>`:

```html
<script type="application/json" id="report-data">
{"charts": {
  "growth": {"type": "line", "title": "…", "unit": "%", "labels": ["2029", "2030", "2031"],
             "datasets": [{"label": "…", "data": [2.8, 3.1, 3.6]}]},
  "mix":    {"type": "doughnut", "title": "…", "labels": ["…", "…"], "datasets": [{"label": "…", "data": [62, 38]}]}
}}
</script>
```

- `type`: `bar`, `horizontalBar`, `stackedBar`, `line`, `area`, `pie`, `doughnut` or `radar`. Values in `data` are plain numbers (no strings, no units); `unit` is optional.
- One chart, one scale: never two measures of different size in one chart; use two charts.
- At most 8 series in a chart, at most 3 in a pie or doughnut-like comparison of categories that are not parts of one whole.
- Prefer a KPI tile or a table to a chart for one or two numbers.

## Colour, dark and light
- The kit has a light and a dark theme and a theme toggle; write no colours of your own on text or backgrounds.
- To change the look ("غيّر الألوان", "use green"), set only these variables in one `<style>` block, as 6-digit hex values: `:root { --kit-accent: #0f766e; --kit-series-1: #0f766e; … }`.
- Use logical properties in any `style` you write (`margin-inline-start`, `padding-block`, `inset-inline-end`, `inline-size`), never left/right, so the page mirrors between Arabic and English.

## Figures and sources
- Use only the figures in the findings, the passages and the user's message when the turn has them; a figure you add from general knowledge is allowed, and the platform marks every figure it cannot match to a source as unverified.
- Never write the unverified marker or a sources section yourself: the platform adds both.
