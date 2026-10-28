Execute phase 08 (kb capability) per CLAUDE.md and README §15 (and the README sections on knowledge bases, plugins and permissions). Branch: phase/08-kb, from the latest main. Precondition: PR #14 (phase 06) is merged; if not, stop and tell me.
Read docs/DECISIONS.md, docs/PROGRESS.md, docs/BACKLOG.md and docs/RESILIENCE.md first. Threat model: ADR-0054. First commit: this prompt verbatim as docs/PHASE-08-BUILD-PROMPT.md, the restated done-criteria, the checklist and the ground rules below under "## Phase 08: Knowledge bases" in docs/PROGRESS.md. New ADRs continue from the last number. If this message arrives cut off, say so and wait for the rest.

DONE WHEN: the CLAUDE.md phase 08 row (KB CRUD creates a Keycloak role; plugin toggles from /v1/capabilities; cross-KB retrieval with attribution; negative permission tests at gateway AND agent), plus the owner decisions and carried fixes below; make test, make eval once (all suites), make smoke and make smoke-airgap green, recorded per rule 11.

OWNER DECISIONS (record as ADRs, decided):
- D1. Phase order changed: phase 08 now, then a standalone "report" PR (HTML report artifacts, pulled forward from phase 11), then phase 07 (planner). Update CLAUDE.md's phase table note and docs/BACKLOG.md accordingly.
- D2. Knowledge bases are filled ahead of time, never by searching the internet at question time: the platform answers from what is already ingested. Sources enter by admin upload in this phase; an automated fetcher in a connected zone is a later decision.
- D3. Every KB document carries source metadata: title, publisher, source URL (optional), publication date, and an optional "supersedes" link to an earlier version of the same source. Retrieval prefers the latest version of a source; an answer citing a figure states the source and its date; when two versions disagree, the answer uses the latest and may note the change.
- D4. Proof of concept KB «الاقتصاد الدولي» / "International economy", for analyst and evaluator, seeded by `make kb-poc` from the freely downloadable public sources the owner listed (IMF Article IV China 2025 report and press release, IMF WEO April and July 2026 pages, stats.gov.cn and english.gov.cn releases, english.scio.gov.cn, fmprc.gov.cn and mfa.gov.cn pages, SPA, SAMA, PIF and Aramco news pages, the World Bank blog post on Hormuz, qna.org.qa, bricspolicycenter.org, sab.com, un.china-mission). Paywalled or rating-agency pages (Moody's, Fitch, S&P, Bloomberg) are excluded and listed as "upload by subscription". Downloaded content is never committed to git; `make kb-poc` fetches it in development only, with a manifest (URL, retrieved date, hash) committed instead. Under smoke-airgap the POC KB is absent and nothing depends on it. Evals use generated fake documents as always (ADR-0065).
- D5. Keyword-free routing (ADR-0247) moves to phase 07: the planner's route decision uses a model on every turn there. Phase 08 keeps keyword candidacy, and KB use is explicit through the user's toggles.

IMPLEMENTATION CHOICES (record as ADRs, decided):
- I1. KB CRUD (admin only, gateway AND agent): create creates realm role kb:<slug>:read; delete is soft (hidden, role kept) with its documents purged from the index; audited fail-closed with actor names.
- I2. Ingestion reuses the phase 04 pipeline and DocumentBackend contract (internal and MCP backends), scoped by KB instead of conversation; same sandbox, limits and resilience rules (rule 12).
- I3. The composer offers KB toggles (from /v1/capabilities, only KBs the user may read); selected_kbs ⊆ permitted checked at the gateway AND the agent; none selected means kb is not used.
- I4. Cross-KB retrieval: hybrid search and rerank across the selected KBs, every citation shows KB name, document title, publisher, date and page; the D3 version preference applied before rerank.
- I5. Admin pages: /admin/kbs (list, create, delete, role shown) and /admin/kbs/<slug> (upload, metadata form for D3, status badges, supersedes picker); Arabic first, Western digits (ADR-0288), code-free.
- I6. Evals: kb.jsonl on generated fake KBs: retrieval hit@8, citation accuracy with KB attribution, latest-version preference (a superseded figure must not be the answer), cross-KB attribution, abstention; thresholds in evals/thresholds.yaml. Routing cases for kb with toggles.
- I7. Negative permission tests: a user without kb:<slug>:read cannot list it, toggle it, retrieve from it, see its citations or its documents, through any endpoint, at the gateway AND the agent; a deleted KB is unreachable.

CARRIED FIXES FROM PHASE 06 (each its own commit, ADR where it changes a decision):
- F1. script_of (services/agent/app/text/script.py): emoji variation selectors, modifiers and joiners count as neutral, never as a writing system. Tests: Arabic, English and mixed answers with 🏖️, ⚙️, flags and skin tones pass; a real disallowed script still fails. The internal-comms suite and the lost routing case must pass again.
- F2. Skill loader (services/agent/app/skills/registry.py): a reference written as ./path, as a Markdown link [text](./path) or as a bare relative path is matched and loaded, so a public skill loads unmodified (ADR-0317, ADR-0318). Tests for each form.
- F3. Answer system prompt (ar and en, version bump): one rule outside {% if has_evidence %}, present on every turn: never invent a URL; a link appears only if it is in the findings or the user's message. Keep the skill precedence line. An eval case where internal-comms writes a newsletter asserts no URL absent from the inputs.
- F4. The reply chip and the skill_forbidden message say «المهارة» / "Skill" instead of «نموذج» / "template".

OUT OF SCOPE: the report capability and HTML artifacts (next PR); PDF export; the planner; automated source fetching; phase 12 backlog.

END
