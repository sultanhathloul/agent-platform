# HANDOFF.md — how Sultan and Claude (chat) run this project

Purpose: everything about our working method that is not in `README.md`, `CLAUDE.md`, `docs/DECISIONS.md` or `docs/PROGRESS.md`. A new chat session with Claude starts by reading those four files plus this one and `docs/PHASE-01-SUMMARY.md`. Nothing from earlier chat transcripts is needed.

## Roles

- **Sultan (owner):** takes decisions, merges PRs, reviews the human-eye files each phase, runs `make clean && make up` when infra config changes.
- **Claude (chat):** designs, writes the prompts for Claude Code, reads its outputs, recommends decisions with their costs. Never edits the repo directly. Answers in Arabic with English terms isolated on their own line or in backticks.
- **Claude Code (build):** implements one phase per session on a branch, records assumptions as ADRs, stops when the done-check is green.
- **Claude Code (review):** always a *fresh* session; reads, never fixes.

## The loop per phase

1. Build session — the phase prompt. Ends green. May take several sessions; re-run the same prompt, PROGRESS.md carries the state.
2. Review session (round 1) — fresh session, full review prompt. Findings only.
3. Owner decision — accept / reject / send back. New "Assumed" ADRs get a status note (Decided / Rejected); ADR text is never rewritten (CLAUDE.md rule 10, ADR-0034).
4. Fix session — same branch, "change nothing else".
5. Review session (round 2) — restricted to the fixes. Last round.
6. Owner merges the PR on GitHub (merge commit, not squash), deletes the branch, updates "Current status" below.
7. Next phase from updated `main`.

### Rules learned in phase 01 (in force from phase 02)

- **Two review rounds per phase, maximum.** Findings after round 2 become tech debt for the next phase, unless they reproduce a cross-user data leak under the threat model.
- **Threat model first (ADR-0054).** Capabilities are trusted first-party code; isolation defends against bugs and accidental coupling, not deliberate attack. Every review prompt states this. Process-level isolation is the phase-09 option before any third-party capability.
- **Merge when the remaining findings need a different layer**, not when the reviewer runs out of ideas.
- **Contracts get the attention:** state schema, ContextPackage, capability contract, policy engine, tier resolver. Everything else is ordinary code.

## Prompt templates

**Build (phase NN):**
```
Execute phase NN (<name>) per CLAUDE.md and README §15 (also §<sections>). Branch: phase/NN-<name>.
Read docs/DECISIONS.md and docs/PROGRESS.md first. Restate the done-criteria, write the checklist to docs/PROGRESS.md, implement, verify continuously.
Done when: <done-check from CLAUDE.md>.
Do not touch anything outside this phase's scope. Stop when the done-check passes.
```
Section map: 02 → §5.1, §7, §8 · 03 → §10 · 04 → §9.3, §18 · 05 → §9.4, §11, §18 · 06 → §9.2, §10.3 · 07 → §9.1, §20 · 08 → §5.2, §19 · 09 → §12, §18.5.

**Review (round 1):**
```
Review branch phase/NN (PR #K) against README.md and CLAUDE.md. Threat model: ADR-0054 (capabilities are trusted first-party code). Do not fix anything and do not commit.
Read docs/DECISIONS.md and docs/PROGRESS.md first, then the code.
Report: (1) ground-rule violations; (2) core files with capability-specific logic; (3) model names, prompt strings, secrets in code, tests bypassing LiteLLM; (4) skipped or tautological tests; (5) PROGRESS claims not matched by code; (6) the three highest-risk items before the next phase, with file paths.
Findings and paths only. No praise.
```

**Fix:**
```
Fix session on branch phase/NN, PR #K. Apply the findings below. Change nothing else. Re-run make test, make eval, make smoke. Push. Stop.
<numbered findings, each with the decision>
Record as tech debt in docs/PROGRESS.md (do not implement): <deferred items>
```

**Review (round 2, restricted, last):**
```
Review branch phase/NN (PR #K), restricted to: <the fixed areas>. Threat model: ADR-0054. Do not fix, do not commit. Report only violations that would force a core change later or a working cross-user data path with reproduction. If none, say "no findings".
```

## Decisions taken in chat (reasoning not fully in the ADRs)

- **ADR-0024, `general` → standard tier.** Fast tier is for instruction-following roles (router, summarizer, judge, extractor), not world knowledge.
- **ADR-0032, temperature per role.** Helpers 0.0; reasoning/reflection 0.2. Declined 0.0 for reasoning: eval flakiness comes from two stable defects ("الريyal" splice → ADR-0033; a wrong flag fact), not sampling.
- **ADR-0046, roles never reach the agent.** Gateway computes `principal.max_tier`; caps are ceilings: `min(caps.default, role cap)`.
- **ADR-0039 + supersession, history stores post-guard text.** The model never re-reads redacted PII; what the user saw is metadata for audit only.
- **ADR-0025, `tool:{name}` is a policy scope; `cap:{slug}` is a permission.**
- **§20, prompts are code.** Versioned files; a prompt change is a PR showing the eval delta. No admin UI for prompts.
- **ADR-0031, streaming vs guards.** Interim: `retract` event, web clears text on any error. Phase 03: DATA on a sentence buffer, OUTPUT `block` policies on the full answer before streaming.
- **ADR-0047–0054, capability isolation** = one exception/return firewall in the core + data-only (JSON) boundary + graph compiled by the core + lint. Never a list of banned APIs; never capability discipline.
- **Known gap, dev only:** DATA redaction does not retract streamed text until phase 03. Written as a gap, not a decision.
- **API version** stays 1.1.0 while changes have not left an unmerged PR.

## Owner checklist each phase

- Read: `config/prompts/**`, `config/policies/**`, `config/models.yaml`, `config/semantic-layer/**`, `infra/keycloak/realm-export.json`, `infra/litellm/config.yaml`, `evals/thresholds.yaml`, new migrations.
- If the Keycloak realm file changed: `make clean && make up`.
- Docker Desktop memory ≥ 12 GB.
- After any model swap: `make eval` three times, record the scores.
- Once per phase before merge: `make smoke` on the offline profile (ADR-0065): `COMPOSE_FILE=docker-compose.yml:docker-compose.local.yml make up smoke`, then `make up` to return to OpenRouter. Needs the four Ollama models in `infra/litellm/config.local.yaml` pulled.
- While the dev tiers are on OpenRouter: no real or ministry data in dev (ADR-0065).
- After merge: update "Current status" here and the phase summary in `docs/`.

## Current status (update by hand)

- **Phase 01: merged to `main` (PR #1) on 2026-09-13.** Sixth restricted review: no findings under the threat model.
- Tech debt into phase 02 (in PROGRESS.md): durable ownership registry; snapshot tests → behaviour tests; untested paths (validation_failed / context_overflow / model_unavailable through the API, capability timeout, client disconnect); ownership check on approve/resume (phase 08) and /v1/debug/context (phase 02); word-level script check (ADR-0033); capabilities that swallow cancellation or leave background tasks (noted in review 6, out of scope).
- Proposed for phase 03: qwen2.5:7b-instruct as fast tier on a helper-role suite (ADR-0030); sentence-buffer guards (ADR-0031).
- Proposed for phase 09: process-level capability isolation (ADR-0054).
- **Next: phase 02 build prompt** (State, Context Assembly, Memory; §5.1, §7, §8; branch `phase/02-context-state-memory`).

## How to resume with Claude (chat)

Open a new chat, attach `docs/HANDOFF.md`, `docs/PHASE-01-SUMMARY.md`, `docs/PROGRESS.md`, `docs/DECISIONS.md` and use the opening message below.
