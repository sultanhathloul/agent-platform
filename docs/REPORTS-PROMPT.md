Standalone PR (owner decision, phase order D1 of phase 08): interactive HTML reports, pulled forward from phase 11. Branch: feat/reports, from the latest main (phase 08 merged; if not, stop and tell me). Commit, push, open the MR; never merge.
Read CLAUDE.md, docs/DECISIONS.md, docs/PROGRESS.md, docs/BACKLOG.md, docs/RESILIENCE.md first. Threat model: ADR-0054. First commit: this prompt verbatim as docs/REPORTS-PROMPT.md and a section "## Interactive reports (standalone PR, owner decision)" in docs/PROGRESS.md with baseline counts, done-check and checklist. New ADRs continue from the last number. If this message arrives cut off, say so and wait for the rest.

GOAL (owner): like Claude's artifacts. The user asks for a dashboard, report, indicators board, infographic or presentation-style page; the answer is a complete, beautiful HTML page built live in a side panel beside the chat, from any source the turn already has: the model's general knowledge, selected knowledge bases, attached files.

OWNER DECISIONS (ADRs, decided):
- D1. A report is an output form, not a data source: capabilities, KB toggles and attachments work as today; the answer node writes HTML instead of text. A report turn applies no text skill.
- D2. Figures from general model knowledge are allowed but visibly marked «غير موثق» / "Unverified"; figures from KBs or files carry their source. Never unmarked.
- D3. Selection: automatic by keywords («داشبورد», «لوحة مؤشرات», «لوحة معلومات», «تقرير تفاعلي», «إنفوجرافيك», «عرض», dashboard, infographic, report page…) through the router as skills are, or explicitly from an output picker in the composer («نص» / «تقرير تفاعلي»).
- D4. Iteration: a follow-up in the same conversation ("غيّر الألوان", "أضف رسمًا للتضخم") edits the latest report and creates a new version of it; earlier versions stay viewable.
- D5. PDF export is out of scope (later).

IMPLEMENTATION CHOICES (ADRs, decided):
- I1. Generation: role report_writer on the heavy tier (config/models.yaml), max output 16k tokens. Report turns use their own deadline profile in config/turn.yaml (deadline 300 s, answer ceiling 270 s, gateway stream bound deadline + margin), validated by app/resilience/budget.py and shown in /admin/settings. The HTML streams to the panel as it is written.
- I2. Design kit served by the platform, never from the internet: config/artifacts/design-system.md (the prompt part: layout, cards, KPI tiles, tables, charts, typography, spacing, dark and light, RTL with logical properties, English terms in <bdi>) and static assets at a same-origin path: kit.css, self-hosted IBM Plex Sans Arabic, Chart.js pinned and vendored. Chart data lives in one <script type="application/json" id="report-data"> block that a kit helper renders, so figures are checkable.
- I3. Figure check after generation (linear, size-capped): every number in the visible text and in report-data is matched against the turn's findings and the user's message (reuse the phase 08 number folding); unmatched figures are wrapped in the kit's unverified marker and logged; a «المصادر» section is appended from the citations (KB, title, publisher, date, page).
- I4. Safety: rendered in an iframe with sandbox="allow-scripts" (no same-origin), served from its own path with CSP default-src 'none', scripts, styles and fonts only from the kit path and inline, connect-src 'none', no external URLs; size cap 512 KB per version; guard OUTPUT/DATA policies run on the report's visible text.
- I5. Storage and access: MinIO artifacts/<tenant>/<conversation>/<id>/v<n>.html plus a DB table (owner, conversation, title, versions). Only the conversation's owner reads it, checked at the gateway AND the agent. History shows the report card.
- I6. API (minor version bump, additive): events artifact_start {id, title}, artifact_delta, artifact_done {id, version, size}; GET a version for the panel; download returns one self-contained file (kit CSS, JS and fonts inlined) that opens offline.
- I7. Web: a side panel like Claude's artifacts, chat on the right and panel on the left in RTL (mirrored in English): live build, tabs «معاينة» / «الكود», buttons download, full screen, copy code, version switcher, close; a report card in the chat reopens it. Arabic first, Western digits (ADR-0288).
- I8. Evals (reports.jsonl, about 12 cases: Arabic, Gulf, English; general knowledge, KB, attached file; one iteration case). Gated: HTML parses, dir=rtl for Arabic, kit only and no external URL, KB figures match their source, general-knowledge figures marked, sources section present, size cap. Reported: visual quality.
- I9. Smoke: a report turn produces a version served with the CSP headers; the download has no external reference; another user cannot fetch it; a follow-up creates v2.

ALSO (first commits): the superseded-version tie (BACKLOG: the April 2026 WEO page cited instead of July) and, if docs/BACKLOG.md recorded a cause for the agent restart of phase 08 run 3 as a crash or failing healthcheck, fix it.

OUT OF SCOPE: PDF export; the planner; editing reports by hand in the panel.

FINISH: from `make clean && make models && make up`: make test, make eval (all suites, once), make smoke, make smoke-airgap; record per CLAUDE.md rule 11. List pages for the owner's visual check (the panel live, versions, download opened offline, a dark and a light report, an Arabic and an English report). Push; open the MR. Do not touch docs/HANDOFF.md.
