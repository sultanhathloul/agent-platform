# Progress

One section per phase (`README.md §15`). At the start of a phase session, write its checklist here before any code. At the end, record what is done, what is left, and how to verify it.

A phase is done only when `make test`, `make eval` (the suites that exist so far) and `scripts/smoke.sh` are all green.

## Phase 01: Skeleton & platform

- Branch: `phase/01-skeleton`
- Status: in progress
- Done when: `make up` green; login works; `general` capability answers a question with streaming; Langfuse shows a trace with context block token counts.

Scope restated:
1. Build the repo skeleton and a compose stack with health checks: postgres, redis, keycloak, litellm, minio, langfuse (web, worker, clickhouse), otel-collector, agent, gateway and web. `make up` brings it up seeded.
2. Keycloak realm `agent` with PKCE client `web`, seed users and `cap:*`/`kb:*:read` roles (ADR-0006). The gateway validates the JWT and builds the principal.
3. The agent runs the capability-agnostic LangGraph graph: capability registry, `run_capabilities` and `general` only. Model calls go through Context Assembly and `models.call` to LiteLLM.
4. One trace per turn flows to Langfuse through the OTel collector, and the `context.assemble` span carries per-block token counts.
5. Done = `make up` green, browser login, a streamed `general` answer, a Langfuse trace with block token counts, and `make test`, `make eval` and `make smoke` green.

Checklist:
- [ ] Docs: README aligned with ADR-0006 and ADR-0010; ADRs 0006–0010 recorded
- [ ] Skeleton: `.gitignore`, `.env.example`, `scripts/init_env.sh` (creates `.env` with random secrets), `Makefile` (`up down seed test eval smoke logs openapi`)
- [ ] Compose: all phase 01 services with health checks; `litellm-mock` and test runners under the `test` profile; `docker-compose.dev.yml` (ports, agent hot reload)
- [ ] Postgres init: databases `app`, `business`, `keycloak`, `langfuse` with one owner role each; `vector` extension in `app`
- [ ] Keycloak realm: clients `web` (public, PKCE S256, `gateway` audience, `tenant_id` claim) and `gateway` (confidential, service account); roles `user` ⊃ `cap:general`, `admin`, `cap:{sql,web,docs,kb}`, `kb:moe:read`; group `moe-analysts`; seed users `admin`, `analyst`, `viewer`
- [ ] LiteLLM: aliases `fast`, `standard`, `heavy`, `embed` → host Ollama; mock config for tests; `config/models.yaml` (tiers, roles)
- [ ] Langfuse v3 headless init (org, project, API keys from `.env`); telemetry off everywhere
- [ ] Agent: FastAPI app, `/v1/health`, `POST /v1/turns` (SSE), `app/api/events.py` → `docs/events.schema.json`, `docs/openapi/agent.json`
- [ ] Agent: `AgentState` (§5.1); graph `load_identity → guard(INPUT) → assemble_context → route → guard(TOOL_LIST) → run_capabilities → answer → validate → guard(OUTPUT) → guard(DATA) → persist_state`
- [ ] Agent: capability contract + registry (manifest validation, fail fast); `general` capability; manifest routing rules
- [ ] Agent: `ContextAssembler` interface + minimal steps with per-block token counts; prompt loader (front-matter `id`, `version`); answer prompts in ar and en
- [ ] Agent: models gateway (LiteLLM client, streaming, usage) + minimal tier resolver (`model.tier_decision` on the span, `MODEL_TIER` hook)
- [ ] Agent: seven checkpoints as pass-through hooks; tool registry + broker skeleton (`PRE_TOOL`/`POST_TOOL`, timeout, retries)
- [ ] Agent: OTel → collector → Langfuse; `turn` root span with conversation/tenant/hashed-user attributes; `context.assemble`; `model.call` generations
- [ ] Gateway: Spring Boot 3.5 / Java 21 / Gradle; JWT (issuer, audience, expiry, JWKS); principal; `GET /v1/me`; `POST /v1/chat/turns` SSE proxy (bytes unchanged); `selected_kbs ⊆ permitted`; `audit_events` (Flyway); OTel + traceparent; CORS; ar/en error envelope; `docs/openapi/gateway.json`
- [ ] Web: Next.js 15 + TS + Tailwind + next-intl (ar default, RTL); oidc-client-ts PKCE login/logout; `/chat/new` → `/chat/[id]`; streamed answers; generated API and event types; every string in `ar.json` and `en.json`
- [ ] Tests: agent unit + integration through mock LiteLLM; gateway (JWT, authz, proxy); web (SSE parser, i18n key parity); `make test` green
- [ ] Evals: `evals/run_evals.py` (golden + `capabilities/*/evals` discovery); `general` suite in ar and en; `evals/thresholds.yaml`; `make eval` green
- [ ] Smoke: health → PKCE login → `/v1/me` → 401 checks → streamed Arabic `general` turn → Langfuse trace with block token counts; `make smoke` green
- [ ] Wrap-up: PROGRESS done/left/verify; list of `config/` and `infra/` files for human review

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
