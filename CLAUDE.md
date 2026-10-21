# CLAUDE.md — working agreement for this repository

You are building the Sovereign Agent Platform described in `README.md`. Read `README.md` fully before any task. `README.md` is the specification; this file is how we work.

## Repository and git

- This project lives in a git repository initialized by the owner with `main` as the default branch and `README.md` + `CLAUDE.md` as the first commit. If you find no `.git` directory, stop and ask; do not initialize one yourself.
- Never commit directly to `main`. Every phase is a branch `phase/NN-name` created from the latest `main`.
- You may commit, push the branch, and open a merge request. You never merge into `main`; the owner does after review.
- Add a `.gitignore` in phase 01 covering `.env`, `node_modules/`, `.venv/`, `__pycache__/`, `build/`, `dist/`, model weights and `*.log`.

## Ground rules

1. **One phase per session.** Work only on the phase named in the task. Do not start the next phase. Phases are `README.md §15`.
2. **Closed decisions stay closed.** `README.md §2` and `§18–§20` are not up for debate. If you believe a decision is wrong, write the argument in `docs/DECISIONS.md` under "Proposed", keep implementing the current decision, and tell me.
3. **Never touch the core to add a capability.** Capabilities live only in `services/agent/app/capabilities/<slug>/`. If a capability needs a core change, stop and explain why before changing the core.
4. **No model names in code.** Use `models.call(role=..., step=...)`. Tiers and aliases live in `config/models.yaml` and `infra/litellm/config.yaml`.
5. **No prompts in code.** Prompts are files under `config/prompts/` or `capabilities/*/prompts/` with front-matter `id` and `version`.
6. **Every tool call goes through `tools/broker.py`; every model call goes through Context Assembly and LiteLLM.** Do not bypass either, not even in tests.
7. **Arabic is first-class.** Every user-facing string goes in `services/web/src/i18n/ar.json` and `en.json`; every guard checker and chunker gets Arabic test cases.
8. **Tests and evals are part of the phase.** A phase is done when `make test`, `make eval` (the suites that exist so far) and `scripts/smoke.sh` are green. Do not declare done otherwise.
9. **Small commits, conventional messages** (`feat(agent): …`, `fix(gateway): …`, `test(guardrails): …`). One PR per phase on a branch `phase/NN-name`.
10. **Record every non-trivial choice** in `docs/DECISIONS.md` (ADR format: context, decision, consequences). An ADR's text is immutable: only its Status line may change, and only with a one-line note, `Accepted on <date>` or `Superseded by ADR-NNNN on <date>`. Every other change to a decision is a new entry that supersedes the old one (ADR-0034).
11. **Numbers, not words.** After every green run, record in `docs/PROGRESS.md` the passed / failed / skipped counts per layer (agent, gateway, gateway-db, web), smoke checks passed/total, and each eval suite score with its dataset run id. "BUILD SUCCESSFUL" or "green" alone is not a result.
12. **No shared resource lets one party stall others.** Read docs/RESILIENCE.md first. Applies to every resource a request uses, inbound (uploads, streams, any client request) and outbound (model provider, databases, Redis, object storage, MCP, web fetch):
   a. Limits at three levels: per user, per tenant and platform-wide. Waiting for a slot is bounded; past the bound, refuse at once with an Arabic and English message (load shedding). No unbounded queue.
   b. Each request gets its own session or connection from a bounded pool; no request can hold a resource another request is waiting on.
   c. One deadline per call, from send to last byte; never only a per-read timeout. Nested timeouts are recorded in docs/RESILIENCE.md (timeout budget); an inner timeout is shorter than the one enclosing it.
   d. Work on input from outside the process is linear and size-capped before parsing; anything heavier runs in a separate process. Nothing CPU-bound runs on the event loop.
   e. Shared state (breakers, caches) changes only on the remote side's own outcome, never on a caller's cancellation. Caches are keyed per user or tenant.
   f. A cancelled call releases everything it holds and aborts its remote request. Work that continues in the background is bounded and de-duplicated.
   g. Retries are bounded, with exponential backoff and jitter.
   h. Tested from the first build, never only by stopping a dependency: a hung dependency, a trickling one and a flooding one, each while another user's request runs. Each test asserts that user's platform-side time (excluding model generation) against a number and, in async services, the event-loop lag.
   i. Prefer a maintained library or infrastructure component. Write custom code only where none fits, with an ADR naming the alternatives checked and why each was rejected; add the row to docs/RESILIENCE.md.
   New code follows this rule from phase 06. Existing gaps stay in the phase 12 backlog.
13. **Evals by what changed.** Prompts, models, routing, retrieval, a capability manifest or an eval dataset changed → run the affected eval suites. Infrastructure only (connections, timeouts, guards, interfaces, UI) → make test and make smoke, no eval. Before merge, make eval runs three times only if something in the first class changed since the last three passing runs; otherwise once. A failed smoke or eval attempt is recorded in PROGRESS with its evidence (trace, audit row), never silently rerun.

## Session protocol

At the start of each session:
1. Read `README.md`, `docs/DECISIONS.md`, and `docs/PROGRESS.md`.
2. Restate in 5 lines what the phase requires and what "done" means (from §15). Wait for my confirmation only if something is ambiguous; otherwise proceed.
3. Plan the work as a checklist and write it to `docs/PROGRESS.md` under the phase heading before writing code.

During the session:
- Run the relevant tests after each meaningful change; do not batch verification to the end.
- When a spec detail is missing, pick the simplest option consistent with the architecture, implement it, and note it in `docs/DECISIONS.md` under "Assumed".
- Keep `make up` working at all times. A broken compose stack is a P0 for the session.

At the end of the session:
- Update `docs/PROGRESS.md`: what is done, what is left, how to verify.
- List any files under `config/` or `infra/` that a human must review (policies, realm, semantic layer).
- Stop. Do not start the next phase.

## Phase plan (from README §15) and the done-check for each

| Phase | Branch | Done when |
|-------|--------|-----------|
| 01 Skeleton & platform | `phase/01-skeleton` | `make up` green; login works; `general` capability answers a question with streaming; Langfuse shows a trace with context block token counts |
| 02 State, Context Assembly, Memory | `phase/02-context-state-memory` | `AgentState` checkpointed; assembler pipeline with budget logging and `NeedsMore`; summarizer kicks in past 8k tokens; memory write/retrieve; `/settings/memory`; `/v1/debug/context` |
| 03 Guardrails engine | `phase/03-guardrails` | policy model in DB + Redis reload; 7 checkpoints; `regex`, `presidio` (Saudi recognizers), `keyword_list`, `llm_judge`; admin policy UI with test panel; `guardrails.jsonl` ≥ threshold |
| 04 docs capability | `phase/04-docs` | upload → indexed badge; hybrid retrieval + rerank; page citations; `rag.jsonl` ≥ threshold |
| 05 MCP integration | `phase/05-mcp` | MCP client as a broker tool provider with the `config/mcp.yaml` allowlist; declarative MCP capability (manifest, prompts and evals, no Python); reference MCP servers in compose; a routing capability (slug `directions`, ADR-0215) on the reference routing server with zero core change; `MCPDocumentBackend` passes the `DocumentBackend` contract suite against the reference RAG MCP server and `rag.jsonl` runs on both backends; every MCP call guarded and audited through the broker; internal servers work under `smoke-airgap`; external servers disabled |
| 06 Skills | `phase/06-skills` | `skills/<slug>/SKILL.md` registry (name and description in Arabic and English, when to use, the capabilities or MCP tools it needs, the Keycloak role that may use it); the router selects a capability and at most one skill; only the selected skill's instructions are loaded, as a reviewed procedural block; text outputs only; no scripts; per-skill evals for format adherence plus routing cases; two reference skills (official memo, official letter); adding a third skill needs no core change |
| 07 Planner | `phase/07-planner` | compound requests produce a plan of steps (capability, MCP tool or skill), each step's inputs may reference earlier steps' outputs; simple requests keep single-step routing with no added latency; capabilities declare structured output schemas in their manifests; dependent steps run in order and independent ones in parallel, every step through the broker; step and time budgets; when a step fails, a partial answer that states what is missing; plan progress shown to the user; `planning.jsonl` ≥ threshold; reference compound case: docs → routing (reference MCP server) → official memo skill |
| 08 kb capability | `phase/08-kb` | KB CRUD creates Keycloak role; plugin toggles from `/v1/capabilities`; cross-KB retrieval with attribution; negative permission tests at gateway AND agent |
| 09 web capability | `phase/09-web` | SearXNG + fetch loop within limits; untrusted sanitization; citation validation fails on fabricated URL (test) |
| 10 sql capability | `phase/10-sql` | semantic layer indexed; sqlglot validation; EXPLAIN gate; `agent_ro` execution; self-correction ≤2; results table + CSV; `sql.jsonl` ≥ threshold |
| 11 Reflection, tiers, HITL and artifact outputs | `phase/11-reflection-hitl` | tier resolver with logged decisions; reflection rules; validation escalation; interrupt/resume path exercised by `smoke.sh`; `routing.jsonl` with `expected_tier`; artifact outputs (a capability or skill returns a file such as an HTML page, stored in MinIO, shown in a sandboxed preview, downloadable, its text through runtime DATA policies); long-running tool calls run as background jobs with progress shown to the user and resumable state (`docs/BACKLOG.md`, MCP items 7 and 8) |
| 12 Evaluation & hardening | `phase/12-hardening` | CI runs all suites with `evals/thresholds.yaml`; online judge sampling; `docs/RUNBOOK.md`; k6 load test 20 users; `email_proofreader` reference capability passes the zero-core-change check |

## How to verify a phase (commands you must be able to run)

```
make up            # full stack, seeded
make test          # unit + integration
make eval          # golden suites implemented so far
make smoke         # scripts/smoke.sh end-to-end
make logs s=agent  # tail a service
```

## Things you must never do

- Commit secrets, `.env`, or model weights.
- Change `docs/openapi/*.json` without bumping the API version, except while the version has not left an unmerged PR (ADR-0046).
- Add a dependency on an external SaaS.
- Give the model write access to any database.
- Mark a phase done with failing or skipped tests.
