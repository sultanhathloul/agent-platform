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
10. **Record every non-trivial choice** in `docs/DECISIONS.md` (ADR format: context, decision, consequences). Never rewrite past entries.

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
| 05 kb capability | `phase/05-kb` | KB CRUD creates Keycloak role; plugin toggles from `/v1/capabilities`; cross-KB retrieval with attribution; negative permission tests at gateway AND agent |
| 06 web capability | `phase/06-web` | SearXNG + fetch loop within limits; untrusted sanitization; citation validation fails on fabricated URL (test) |
| 07 sql capability | `phase/07-sql` | semantic layer indexed; sqlglot validation; EXPLAIN gate; `agent_ro` execution; self-correction ≤2; results table + CSV; `sql.jsonl` ≥ threshold |
| 08 Reflection, tiers, HITL | `phase/08-tiers-reflection-hitl` | tier resolver with logged decisions; reflection rules; validation escalation; interrupt/resume path exercised by `smoke.sh`; `routing.jsonl` with `expected_tier` |
| 09 Evaluation & hardening | `phase/09-hardening` | CI runs all suites with `evals/thresholds.yaml`; online judge sampling; `docs/RUNBOOK.md`; k6 load test 20 users; `email_proofreader` reference capability passes the zero-core-change check |

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
- Change `docs/openapi/*.json` without bumping the API version.
- Add a dependency on an external SaaS.
- Give the model write access to any database.
- Mark a phase done with failing or skipped tests.
