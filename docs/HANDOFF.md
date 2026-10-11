# HANDOFF.md — how Sultan and Claude (chat) run this project

Purpose: everything about our working method that is not in `README.md`, `CLAUDE.md`, `docs/DECISIONS.md` or `docs/PROGRESS.md`. A new chat session with Claude starts by reading those four files plus this one and the latest phase summaries (`docs/PHASE-02-SUMMARY.md`, `docs/PHASE-03-SUMMARY.md`). Nothing from earlier chat transcripts is needed.

## Roles

- **Sultan (owner):** takes scope and product decisions, merges PRs, reviews the human-eye files and the admin pages each phase, runs `make clean && make up` when infra config changes.
- **Claude (chat):** designs, writes the prompts for Claude Code, reads its outputs, recommends decisions with their costs. Never edits the repo directly.
  - Answers in Arabic; every English term goes on its own line or in backticks, never mixed inside an Arabic sentence.
  - Explains every point with a simple, concrete example.
  - Brings Sultan only scope decisions and product decisions management would see. Implementation details are settled by Claude and stated in the Claude Code prompt.
  - Gives opinionated recommendations with their cost, not option lists.
- **Claude Code (build):** implements one phase per session on a branch, records assumptions as ADRs, stops when the done-check is green.
- **Claude Code (review / verify):** always a *fresh* session; reads, never fixes.

## The loop per phase

1. Build session — the phase prompt. Ends green. May take several sessions; re-run the same prompt, PROGRESS.md carries the state.
2. Owner-decisions session (only if the owner adds or changes scope after the build) — same branch, before review, so the new contract gets the full round-1 review.
3. Review session (round 1) — fresh session, full review prompt. Findings only.
4. Owner decision — accept / reject / send back. New "Assumed" ADRs get a status note (Decided / Rejected); ADR text is never rewritten (CLAUDE.md rule 10, ADR-0034).
5. Fix session — same branch, "change nothing else".
6. Review session (round 2) — restricted to the fixes. Last round.
7. Leak verification (only if round 2 or a later verification finds a path that exposes one user's text or data to another) — narrow fix, then a fresh verification restricted to that fix. Not a review round.
8. Owner visual check of every UI the phase touched (screenshots to Claude chat); UI fix sessions as needed, no review round.
9. Owner merges the PR on GitHub (merge commit, not squash), deletes the branch, updates "Current status" below, commits this file on `main`.
10. Next phase from updated `main`.

### Rules learned (in force)

From phase 01:
- **Two review rounds per phase, maximum.** Findings after round 2 become tech debt for the next phase, unless they reproduce a cross-user data leak under the threat model.
- **Threat model first (ADR-0054).** Capabilities are trusted first-party code; isolation defends against bugs and accidental coupling, not deliberate attack. Every review prompt states this. Process-level isolation is the phase-09 option before any third-party capability.
- **Merge when the remaining findings need a different layer**, not when the reviewer runs out of ideas.
- **Contracts get the attention:** state schema, ContextPackage, capability contract, policy engine, tier resolver. Everything else is ordinary code.

From phase 03:
- **What reopens a PR after round 2:** only a working path that exposes one user's text or data to another user or tenant. A same-tenant yes/no signal about a text the user must guess word for word (timing, cache hits) is recorded as a note in phase 09 tech debt and never reopens the PR.
- **Owner adds scope → decisions session before review**, so the new contract gets a full review, not a restricted round 2.
- **Ground rules live in the repo**, never only in the prompt: permanent ones in `CLAUDE.md` (rule 11: test counts per layer), phase-specific ones in that phase's section of `PROGRESS.md`. A reviewer cannot check what it cannot read.
- **Every prompt states the implementation choices already made** (as "decided, do not reopen"), so the build session does not fill gaps with its own assumptions on contracts.
- **The tool, not the content.** A phase delivers a manageable tool; which policies, prompts or topics are enabled, and their wording, are management decisions taken at activation and edited from the admin UI. Eval cases are not content: they measure the tool, so their labels are checked.
- **Visual check before merge.** Tests do not catch mixed-direction text, untranslated labels or admin forms that need code. Screenshots of every touched page go to Claude chat before merge.

## Prompt templates

**Build (phase NN):**
```
Execute phase NN (<name>) per CLAUDE.md and README §15 (also §<sections>). Branch: phase/NN-<name>.
Read docs/DECISIONS.md and docs/PROGRESS.md first. Restate the done-criteria, write the checklist and the phase ground rules below to docs/PROGRESS.md under the phase heading, implement, verify continuously.
Done when: <done-check from CLAUDE.md>, plus <tech debt items brought into scope>.
PHASE GROUND RULES: <G-rules>
OWNER DECISIONS (fixed; record as ADRs, status decided): <D-items>
IMPLEMENTATION CHOICES ALREADY MADE (record as ADRs, status decided; do not reopen): <I-items>
SCOPE, IN THIS ORDER: <parts>
OUT OF SCOPE: <items>
END OF SESSION: update PROGRESS per CLAUDE.md rule 11; list files for human review. Do not touch docs/HANDOFF.md.
Do not touch anything outside this phase's scope. Stop when the done-check passes.
```
Section map: 04 → §9.3, §18 · 05 → §9.4, §11, §18 · 06 → §9.2, §10.3 · 07 → §9.1, §20 · 08 → §5.2, §19 · 09 → §12, §18.5.

**Owner decisions (after build, before review):**
```
Owner decisions session on branch phase/NN, PR #K. Apply the decisions below and nothing else. Read docs/DECISIONS.md and docs/PROGRESS.md first. Threat model: ADR-0054.
STATUS NOTES ON ASSUMED ADRs: <ADR: Accepted / Decided / Rejected, superseded by …>
DECISION 1..n: <decision, tests>
FINISH: make test, make eval three times, make smoke twice. Record per CLAUDE.md rule 11. Push. Stop. Do not touch docs/HANDOFF.md.
```

**Review (round 1):**
```
Review branch phase/NN (PR #K) against README.md and CLAUDE.md. Threat model: ADR-0054 (capabilities are trusted first-party code). Do not fix anything and do not commit.
Read docs/DECISIONS.md and docs/PROGRESS.md first, then the code.
Report: (1) ground-rule violations, including the phase ground rules in PROGRESS; (2) core files with capability-specific logic; (3) model names, prompt strings, secrets in code, tests bypassing Context Assembly or LiteLLM; (4) skipped or tautological tests; (5) PROGRESS claims not matched by code; (6) the three highest-risk items before the next phase, with file paths.
Check specifically: <a.. per phase>
Known and already decided, do not report as findings: <items>
Findings and paths only. No praise.
```

**Fix:**
```
Fix session on branch phase/NN, PR #K. Apply the decisions below. Change nothing else. Read docs/DECISIONS.md and docs/PROGRESS.md first. Threat model: ADR-0054.
<numbered items, each with the decision and its tests>
Record as tech debt in docs/PROGRESS.md (do not implement): <deferred items>
FINISH: make test, make eval three times, make smoke twice. Record per CLAUDE.md rule 11. Push. Stop. Do not touch docs/HANDOFF.md.
```

**Review (round 2, restricted, last):**
```
Review branch phase/NN (PR #K), restricted to: <the fixed areas>. Threat model: ADR-0054. Do not fix, do not commit. Report only violations that would force a core change later, or a working path by which one user's data or one tenant's policy reaches another user or tenant, with reproduction. If none, say "no findings".
```

**Leak verification (after a narrow leak fix):**
```
Verify branch phase/NN (PR #K), restricted to the fix for <finding>. Threat model: ADR-0054. Do not fix, do not commit.
Answer only: <2–3 yes/no questions covering every path, e.g. every way a stream can end>. Give a reproduction for any yes. <Accepted residuals, e.g. same-tenant timing>: do not report them. If none, say "no findings".
```

## Decisions taken in chat (reasoning not fully in the ADRs)

Phases 01–02:
- **ADR-0024, `general` → standard tier.** Fast tier is for instruction-following roles (router, summarizer, judge, extractor), not world knowledge.
- **ADR-0032, temperature per role.** Helpers 0.0; reasoning/reflection 0.2. Declined 0.0 for reasoning: eval flakiness comes from two stable defects ("الريyal" splice → ADR-0033; a wrong flag fact), not sampling.
- **ADR-0046, roles never reach the agent** — except Keycloak roles prefixed `policy:` since phase 03 (see below). Gateway computes `principal.max_tier`; caps are ceilings: `min(caps.default, role cap)`.
- **ADR-0039 + supersession, history stores post-guard text.** The model never re-reads redacted PII; what the user saw is metadata for audit only.
- **ADR-0025, `tool:{name}` is a policy scope; `cap:{slug}` is a permission.**
- **§20, prompts are code.** Versioned files; a prompt change is a PR showing the eval delta. No admin UI for prompts.
- **ADR-0047–0054, capability isolation** = one exception/return firewall in the core + data-only (JSON) boundary + graph compiled by the core + lint. Never a list of banned APIs; never capability discipline.
- **API version** stays put while changes have not left an unmerged PR (now written into CLAUDE.md). Current: agent and gateway 1.3.0.

Phase 03:
- **Streaming vs guards (ADR-0039 full fix).** DATA and OUTPUT redact run on a sentence buffer; a final whole-answer pass re-sends redacted text if the buffer missed a span. Only an enabled OUTPUT `block`/`rewrite` policy buffers the whole answer.
- **Topic policies cost latency.** Buffered mode: first token about 4.9 s against 2.2 s streaming on a five-sentence answer. Recommendation to management: start a topic policy at INPUT only (no buffering), add OUTPUT only with evidence of answers drifting.
- **Exceptions (ADR-0108, ADR-0115).** A narrower policy may weaken a wider one, by design, but only by containment: `global` contains every scope; tenant, role, kb and tool do not contain each other, so between them the stricter wins. An exception cancels only the spans its own checker matched; a whole-text verdict (judge) never lifts span-based redaction. kb:/tool: exceptions at OUTPUT/DATA are refused until phase 04 attributes answer parts to sources. Every exception is flagged and audited, including flags induced by saving a wider policy.
- **Policy roles (ADR-0106, ADR-0107).** Per-user policy differences go through Keycloak realm roles prefixed `policy:` (e.g. `policy:pii-exempt`); the gateway forwards only those, they grant nothing. No `user:` scope; a person-level exception inside a tenant with its own strict policy (composite scope) is deferred until needed.
- **Tenant is identity, never a policy device.** Moving a user to a tenant to apply a policy changes the ownership of their conversations and memory (rejected ADR-0104).
- **Admin is a platform operator above all tenants (ADR-0117).** A tenant-scoped administrator would be a separate role, not in v1.
- **Judge cache and client-visible signals (ADR-0116 → ADR-0118).** Verdict cache keyed per tenant; the admin test panel never reads or writes it; clients never see checker detail, confidence, cache status, guard-check usage or tier records; the relay drops incomplete events. Remaining, accepted: same-tenant timing.
- **Saudi national ID / iqama (ADR-0120).** Redacted when the Luhn check digit is valid OR a context word precedes it. "OR", because no official checksum specification is published.
- **TOOL_LIST block is silent (ADR-0121).** It removes the capability and audits; it never shows a message or ends the turn.
- **Seed topics are examples in the Saudi government context**, disabled: political opinions (assessing government decisions or officials, foreign policy, favouring states or parties; neutral explanation allowed) and sectarian/tribal comparisons. Wording is management's at activation.
- **Policy admin is code-free.** Structured editors per checker; JSON only behind "إعدادات متقدمة". Arabic names and descriptions with no Latin tokens inside sentences; technical ids appear only in chips.

## Owner checklist each phase

- Read: `config/prompts/**`, `config/policies/**`, `config/models.yaml`, `config/semantic-layer/**`, `infra/keycloak/realm-export.json`, `infra/litellm/config.yaml`, `evals/thresholds.yaml`, new migrations.
- Screenshots of every page the phase touched, to Claude chat, before merge.
- If the Keycloak realm file changed: `make clean && make up` (never patch the live realm by hand; the repo must be what runs).
- Docker Desktop memory ≥ 12 GB.
- `.env` must hold `CONTENT_HASH_KEY` (`openssl rand -hex 32`); the agent does not start without it.
- After any model swap: `make eval` three times, record the scores.
- After a change to seed policies, the smoke policy-row reference may need a reset once: `rm build/smoke/policy-rows` (tech debt, phase 04).
- Once per phase before merge: `make smoke` on the offline profile, with the internet disconnected after images and models are pulled (ADR-0065, ADR-0066). Replaced by `make smoke-airgap` from phase 04.
  - Offline: the `.env.example` model values (`FAST_MODEL`, `STANDARD_MODEL`, `HEAVY_MODEL`, `EMBED_MODEL`, `MODEL_API_BASE`, `EMBED_API_BASE`, `MODEL_API_KEY=`), `make models`, `make up`; then disconnect, `docker compose restart`, `make smoke`.
  - OpenRouter (development only): the block in README §21.4, then `make up`.
- Evals run as the seed user `evaluator`, with memory switched off by `run_evals.py` (ADR-0074); the guardrails suite runs as `admin` through the policy test endpoint.
- While the dev tiers are on OpenRouter: no real or ministry data in dev (ADR-0065).
- After merge: update "Current status" here, add the phase summary in `docs/`, commit on `main`.

## Current status (update by hand)

- **Phase 03: merged to `main` (PR #3) on 11 Oct 2026.** Build; owner-decisions session (role scope through `policy:*` roles); review round 1; fix; narrow fix; review round 2 (one cross-tenant finding: judge cache); two narrow fixes with restricted verifications; final verification: no findings. Then seed/eval session and two UI sessions from the owner's visual check.
- Results at merge: agent 509, gateway 91, gateway-db 5, web 125 (0 failed, 0 skipped); smoke 76/76; eval guardrails 142/142, general 28/28.
- TTFT p50 (n=20): the sentence buffer adds 0.27 s (one sentence) and 0.69 s (five sentences) against phase 02; buffered mode 4.86 s against 2.23 s streaming on five sentences.
- Dev model tiers on OpenRouter via `.env` (ADR-0065). No real or ministry data in dev.
- Tech debt into phase 04:
  - README §6.1 amendment: the relay rebuilds the terminal event (strips `guard_audit`) and drops incomplete events.
  - `make smoke-airgap`: egress blocked for every container except `litellm`; replaces the offline-profile run.
  - kb:/tool: exceptions at OUTPUT/DATA stay refused until per-source attribution; revisit with the docs capability.
  - Smoke policy-row check: compare counts before and after within one run, not across runs.
- Tech debt into phase 09 (notes, not risks):
  - OUTPUT `rewrite` prompt must carry the policy topic only, never checker detail or the cache marker.
  - Same-tenant timing of cached judge verdicts (ADR-0118).
  - Asynchronous memory write (7.9 s before `done`, measured in phase 02).
  - Production realm export without seed users or passwords.
  - Role removal takes effect only when the 300 s access token expires.
  - Actor display names in the policy change log come from the user-editable Keycloak profile; lock name edits or always show the username beside them.
- Known residual: PII split across lines by the model is caught by the final whole-answer pass and retracted, so it can be visible briefly before the retract.
- Management decisions pending (in `docs/PHASE-03-SUMMARY.md`): which topic policies to enable at go-live, at which checkpoints; memory retention; production model hosting.
- **Next: phase 04 build prompt** (UC3 documents; README §9.3, §18; branch `phase/04-docs`).

## How to resume with Claude (chat)

Open a new chat and attach `docs/HANDOFF.md`, `docs/PHASE-02-SUMMARY.md`, `docs/PHASE-03-SUMMARY.md`, `docs/PROGRESS.md`, `docs/DECISIONS.md`, `README.md` and `CLAUDE.md`. Opening message:

```
نكمل مشروع agent-platform من ملف التسليم.
اقرأ HANDOFF.md أولًا (الأدوار، دورة العمل، القواعد المتعلمة، قوالب الأوامر، الحالة الحالية)، ثم ملخصي المرحلتين الثانية والثالثة، ثم PROGRESS.md وDECISIONS.md، ثم README.md وCLAUDE.md لمعايير المرحلة القادمة.
التزم بتفضيلاتي: المصطلحات الإنجليزية في سطر مستقل أو داخل backticks، مثال مبسط مع كل شرح، ولا تعرض عليّ إلا قرارات النطاق أو المنتج.
المطلوب: <الخطوة التالية من قسم Current status>.
```
