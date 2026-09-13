# Progress

One section per phase (`README.md §15`). At the start of a phase session, write its checklist here before any code. At the end, record what is done, what is left, and how to verify it.

A phase is done only when `make test`, `make eval` (the suites that exist so far) and `scripts/smoke.sh` are all green.

## Phase 01: Skeleton & platform

- Branch: `phase/01-skeleton` — PR #1, https://github.com/sultanhathloul/agent-platform/pull/1
- Status: **done, awaiting review.** `make up`, `make test`, `make smoke` and `make eval` are green. ADR-0024 is decided (option (b)); nothing is blocked.
- Done when: `make up` green; login works; `general` capability answers a question with streaming; Langfuse shows a trace with context block token counts.

Scope restated:
1. Build the repo skeleton and a compose stack with health checks: postgres, redis, keycloak, litellm, minio, langfuse (web, worker, clickhouse), otel-collector, agent, gateway and web. `make up` brings it up seeded.
2. Keycloak realm `agent` with PKCE client `web`, seed users and `cap:*`/`kb:*:read` roles (ADR-0006). The gateway validates the JWT and builds the principal.
3. The agent runs the capability-agnostic LangGraph graph: capability registry, `run_capabilities` and `general` only. Model calls go through Context Assembly and `models.call` to LiteLLM.
4. One trace per turn flows to Langfuse through the OTel collector, and the `context.assemble` span carries per-block token counts.
5. Done = `make up` green, browser login, a streamed `general` answer, a Langfuse trace with block token counts, and `make test`, `make eval` and `make smoke` green.

Checklist:
- [x] Docs: README aligned with ADR-0006 and ADR-0010; ADRs 0006–0010 recorded
- [x] Skeleton: `.gitignore`, `.env.example`, `scripts/init_env.sh` (creates `.env` with random secrets), `Makefile` (`up down seed test eval smoke logs openapi models`)
- [x] Compose: all phase 01 services with health checks; `litellm-mock` and test runners under the `test` profile; `docker-compose.dev.yml` (ports, agent hot reload)
- [x] Postgres init: databases `app`, `business`, `keycloak`, `langfuse` with one owner role each; `vector` extension in `app`
- [x] Keycloak realm: clients `web` (public, PKCE S256, `gateway` audience, `tenant_id` claim) and `gateway` (confidential, service account); roles `user` ⊃ `cap:general`, `admin`, `cap:{sql,web,docs,kb}`, `kb:moe:read`; group `moe-analysts`; seed users `admin`, `analyst`, `viewer`
- [x] LiteLLM: aliases `fast`, `standard`, `heavy`, `embed` → host Ollama; mock config for tests; `config/models.yaml` (tiers, roles)
- [x] Langfuse v3 headless init (org, project, API keys from `.env`); telemetry off everywhere
- [x] Agent: FastAPI app, `/v1/health`, `POST /v1/turns` (SSE), `app/api/events.py` → `docs/events.schema.json`, `docs/openapi/agent.json`
- [x] Agent: `AgentState` (§5.1, additions in ADR-0016); graph `load_identity → guard(INPUT) → assemble_context → route → guard(TOOL_LIST) → run_capabilities → answer → validate → guard(OUTPUT) → guard(DATA) → persist_state`
- [x] Agent: capability contract + registry (manifest validation, fail fast); `general` capability; manifest routing rules
- [x] Agent: `ContextAssembler` interface + minimal steps with per-block token counts; prompt loader (front-matter `id`, `version`); answer prompts in ar and en
- [x] Agent: models gateway (LiteLLM client, streaming, usage) + minimal tier resolver (`model.tier_decision` on the span, `MODEL_TIER` hook)
- [x] Agent: seven checkpoints as pass-through hooks; tool registry + broker skeleton (`PRE_TOOL`/`POST_TOOL`, timeout, retries)
- [x] Agent: OTel → collector → Langfuse; `turn` root span with conversation/tenant/hashed-user attributes; `context.assemble`; `model.call` generations
- [x] Agent: `validate` rejects an Arabic answer containing CJK characters (`app/text/script.py`, ADR-0027)
- [x] Gateway: Spring Boot 3.5 / Java 21 / Gradle; JWT (issuer, audience, expiry, JWKS); principal; `GET /v1/me`; `POST /v1/chat/turns` SSE proxy (bytes unchanged); `selected_kbs ⊆ permitted`; `audit_events` (Flyway); OTel + traceparent; CORS; ar/en error envelope; `docs/openapi/gateway.json`
- [x] Web: Next.js 15 + TS + Tailwind + next-intl (ar default, RTL); oidc-client-ts PKCE login/logout; `/chat/new` → `/chat/[id]`; streamed answers; generated API and event types; every string in `ar.json` and `en.json`
- [x] Tests: agent unit + integration through mock LiteLLM; gateway (JWT, authz, proxy); web (SSE parser, i18n key parity); `make test` green
- [x] Evals: `evals/run_evals.py` (golden + `capabilities/*/evals` discovery); `general` suite, 20 cases in ar and en; `evals/thresholds.yaml`; `make eval` green
- [x] Smoke: health → PKCE login → `/v1/me` → 401 checks → streamed Arabic `general` turn → Langfuse trace with block token counts; `make smoke` green
- [x] Setup: `make models` + `scripts/pull_models.sh`; README §21 "Getting started on a new machine" including a shared model server
- [x] Wrap-up: PROGRESS done/left/verify; list of `config/` and `infra/` files for human review

Session result (2026-09-13), closing the phase:
- **`make up`:** green. 13 long-running services are up, every one with a healthcheck is healthy, and `minio-init` completed. The OTel collector image is distroless, so it has no healthcheck.
- **`make test`:** green.
  - agent: 67 passed (58 before, plus the new `test_validate.py`).
  - gateway: 37 tests, 0 failed, 0 skipped. `make test` serves this layer from cache, so it was re-run for real with `gradle cleanTest test --no-build-cache`; a plain `cleanTest test` still restores `:test FROM-CACHE` and proves nothing.
  - web: 46 passed across 4 files, with typecheck and lint, re-run fresh with `docker build --target test --no-cache-filter test`.
- **`make smoke`:** green, with the tier check now expecting `standard` for a general turn.
- **`make eval`:** green. `general` 18/20 = 0.90 against the 0.85 threshold.
  - Run-to-run variance, three consecutive runs on the same stack: 0.90, 0.85, 0.90. One run sat exactly on the threshold.
  - The two recurring failures are script drift by `qwen2.5:14b-instruct`, not wrong knowledge: `"…هي الريyal السعودي"` and an answer that continues in Chinese. The second is caught by the new `validate` check and ends the turn as `validation_failed`.
  - ADR-0032 proposes pinning the reasoning temperature to 0 in `config/models.yaml` so the gate stops depending on sampling. **Not implemented — it changes behaviour for every capability and is the owner's call.**
- **Browser:** the owner confirmed that sending a message from the UI works. The earlier automation failure was a tool artefact, not a bug.

What changed this session:
- `general.min_tier: fast → standard` (ADR-0024, option (b)), with README §19.4, the manifest, two integration tests and the smoke tier check moved with it.
- `validate` gained the deterministic CJK check for Arabic answers, with `app/text/script.py` and `tests/unit/test_validate.py` (ADR-0027).
- `answer.system` ar went to **1.2.0** with one line requiring Arabic script. It is what moved the suite from 16/20 to 18/20; the two remaining failures are the ones above.
- The `general` suite grew from 8 to 20 cases (13 Arabic, 7 English) at the unchanged 0.85 threshold (ADR-0028).
- README: seven checkpoints in §0, §10.1 (new `MODEL_TIER` row), §15 and §18.4 (ADR-0026); `tool:{name}` documented as a policy scope, never a permission (ADR-0025); new §21 "Getting started on a new machine".
- `make models` + `scripts/pull_models.sh`, and `VLLM_API_KEY` wired to the litellm container (ADR-0029).
- ADR-0011 to ADR-0023 moved from *assumed* to *decided*; ADR-0030, ADR-0031 and ADR-0032 recorded as proposals.
- `.DS_Store` and `docs/.DS_Store` are no longer tracked; `.idea/` and `.vscode/` are ignored.

Left for this phase: **nothing but review.** PR #1 is ready for review; the owner merges it.

How to verify:
- `make up && make test && make smoke && make eval`
- Gateway tests for real: `docker run --rm -v "$PWD/services/gateway":/w -v gateway-gradle-cache:/home/gradle/.gradle -w /w gradle:9.7.1-jdk21 gradle cleanTest test --no-daemon --no-build-cache`
- Web: http://localhost:3000 (`analyst`/`analyst`, `viewer`/`viewer`, `admin`/`admin`).
- Langfuse: http://localhost:3001 (credentials in `.env`).

Human review needed:
- `infra/keycloak/realm-export.json`: roles, groups, the gateway service account's `realm-management` roles, and the user-profile `tenant_id` attribute.
- `infra/litellm/config.yaml`: aliases, `num_ctx` per alias, and the `heavy → standard` fallback.
- `config/models.yaml`: the dev heavy window of 65536.
- `config/prompts/**`: `answer.system@1.2.0` (ar) and `@1.1.0` (en), `context.state_digest@1.1.0`, `context.findings@1.0.0`. The ar and en versions differ on purpose: only the Arabic prompt needed the script rule.
- `config/context.yaml`, `config/messages/errors.yaml`.
- `evals/thresholds.yaml` and `services/agent/app/capabilities/general/evals/general.jsonl` (the 20 cases and their ground truths).
- `infra/otel/collector.yaml`, `infra/postgres/init/00-databases.sh`.

Owner notes:
- **Docker memory:** Docker Desktop has 8 GB and the stack uses about 6.5 GB, with Keycloak capped at a 768 MB heap and the gateway at 768 MB. 12 GB or more is recommended so that `make test` builds have headroom.
- **Eval headroom:** the `general` suite passes with roughly one case of margin. ADR-0032 is the way to remove the luck; until it is decided, expect an occasional 0.85.
- **Reranker:** `bge-reranker-v2-m3` is not in the Ollama library, and Ollama has no rerank API. How to serve it is a phase 04 decision (ADR-0008).
- **Open for phase 03:** ADR-0030 (measure a stronger `fast` model on helper-role work) and ADR-0031 (where the `OUTPUT` and `DATA` guards run relative to streaming).

## Phase 02: State, Context Assembly, Memory

- Branch: `phase/02-context-state-memory`
- Status: not started
- Done when: `AgentState` checkpointed; assembler pipeline with budget logging and `NeedsMore`; summarizer kicks in past 8k tokens; memory write/retrieve; `/settings/memory`; `/v1/debug/context`.

## Phase 03: Guardrails engine

- Branch: `phase/03-guardrails`
- Status: not started
- Done when: policy model in DB + Redis reload; 7 checkpoints; `regex`, `presidio` (Saudi recognizers), `keyword_list`, `llm_judge`; admin policy UI with test panel; `guardrails.jsonl` ≥ threshold.

## Phase 04: UC3 documents (`docs` capability)

- Branch: `phase/04-docs`
- Status: not started
- Done when: upload → indexed badge; hybrid retrieval + rerank; page citations; `rag.jsonl` ≥ threshold.

## Phase 05: UC4 KB plugins (`kb` capability)

- Branch: `phase/05-kb`
- Status: not started
- Done when: KB CRUD creates Keycloak role; plugin toggles from `/v1/capabilities`; cross-KB retrieval with attribution; negative permission tests at gateway AND agent.

## Phase 06: UC2 web (`web` capability)

- Branch: `phase/06-web`
- Status: not started
- Done when: SearXNG + fetch loop within limits; untrusted sanitization; citation validation fails on fabricated URL (test).

## Phase 07: UC1 Text-to-SQL (`sql` capability)

- Branch: `phase/07-sql`
- Status: not started
- Done when: semantic layer indexed; sqlglot validation; EXPLAIN gate; `agent_ro` execution; self-correction ≤2; results table + CSV; `sql.jsonl` ≥ threshold.

## Phase 08: Reflection, model routing, HITL

- Branch: `phase/08-tiers-reflection-hitl`
- Status: not started
- Done when: tier resolver with logged decisions; reflection rules; validation escalation; interrupt/resume path exercised by `smoke.sh`; `routing.jsonl` with `expected_tier`.

## Phase 09: Evaluation & hardening

- Branch: `phase/09-hardening`
- Status: not started
- Done when: CI runs all suites with `evals/thresholds.yaml`; online judge sampling; `docs/RUNBOOK.md`; k6 load test 20 users; `email_proofreader` reference capability passes the zero-core-change check.
