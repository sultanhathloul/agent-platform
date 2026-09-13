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
- [x] Agent: models gateway (LiteLLM client, streaming, usage) + minimal tier resolver (`model.tier_decision` on the span, `MODEL_TIER` hook); decoding temperature per role from `config/models.yaml` (ADR-0032)
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
  - agent: 70 passed (58 before, plus `test_validate.py` and the per-role temperature tests).
  - gateway: 37 tests, 0 failed, 0 skipped. `make test` serves this layer from cache, so it was re-run for real with `gradle cleanTest test --no-build-cache`; a plain `cleanTest test` still restores `:test FROM-CACHE` and proves nothing.
  - web: 46 passed across 4 files, with typecheck and lint, re-run fresh with `docker build --target test --no-cache-filter test`.
- **`make smoke`:** green, with the tier check now expecting `standard` for a general turn.
- **`make eval`:** green, measured in two rounds — before and after the per-role temperature of ADR-0032.
  - Before (one hard-coded 0.2 for every call), three consecutive runs: **0.90, 0.85, 0.90**.
  - After (helper roles 0.0, `reasoning` and `reflection` 0.2), three consecutive runs: **0.85, 0.90, 0.85** — 17/20, 18/20, 17/20 against the 0.85 threshold. All three pass; two sit exactly on it.
  - The change was not expected to steady *this* suite: a `general` turn makes exactly one model call, the answer, and the owner kept `reasoning` at 0.2. What it makes deterministic is every helper call — router, summarizer, judge, extractor — which is where run-to-run comparability starts to matter, from the `llm_judge` checker in phase 03 onward.
  - The recurring failures are the same ones, and all are model defects rather than missing knowledge: `"…هي الريyal السعودي"` (byte-identical in all three runs), the sunrise answer continuing in Chinese, which `validate` catches and ends as `validation_failed`, and the flag colour answered as red in two of the three runs.
  - ADR-0033 is the decided phase 02 follow-up: a word-level rule failing any token that mixes Arabic and Latin letters would catch the `الريyal` class, which no guard catches today.
- **Browser:** the owner confirmed that sending a message from the UI works. The earlier automation failure was a tool artefact, not a bug.

What changed this session:
- `general.min_tier: fast → standard` (ADR-0024, option (b)), with README §19.4, the manifest, two integration tests and the smoke tier check moved with it.
- `validate` gained the deterministic CJK check for Arabic answers, with `app/text/script.py` and `tests/unit/test_validate.py` (ADR-0027).
- `answer.system` ar went to **1.2.0** with one line requiring Arabic script. It is what moved the suite from 16/20 to 18/20; the two remaining failures are the ones above.
- The `general` suite grew from 8 to 20 cases (13 Arabic, 7 English) at the unchanged 0.85 threshold (ADR-0028).
- README: seven checkpoints in §0, §10.1 (new `MODEL_TIER` row), §15 and §18.4 (ADR-0026); `tool:{name}` documented as a policy scope, never a permission (ADR-0025); new §21 "Getting started on a new machine".
- `make models` + `scripts/pull_models.sh`, and `VLLM_API_KEY` wired to the litellm container (ADR-0029).
- Decoding temperature became a per-role setting in `config/models.yaml` — `router`, `summarizer`, `judge`, `extractor` at 0.0, `reasoning` and `reflection` at 0.2 (ADR-0032, accepted by the owner with that change). `ModelGateway` reads it by role, no temperature literal is left in code, and startup fails when a role has no temperature or the value is outside 0.0–2.0. The tier decision's role now drives the span attribute `model.temperature`.
- ADR-0033 recorded as decided for phase 02: extend the script check with a word-level rule failing any token that mixes Arabic and Latin letters, with a small configurable allow-list. **Not implemented in phase 01, by instruction.**
- ADR-0011 to ADR-0023 moved from *assumed* to *decided*; ADR-0030 and ADR-0031 recorded as proposals for phase 03.
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
- `config/models.yaml`: the dev heavy window of 65536, and the `temperature` block (ADR-0032).
- `config/prompts/**`: `answer.system@1.2.0` (ar) and `@1.1.0` (en), `context.state_digest@1.1.0`, `context.findings@1.0.0`. The ar and en versions differ on purpose: only the Arabic prompt needed the script rule.
- `config/context.yaml`, `config/messages/errors.yaml`.
- `evals/thresholds.yaml` and `services/agent/app/capabilities/general/evals/general.jsonl` (the 20 cases and their ground truths).
- `infra/otel/collector.yaml`, `infra/postgres/init/00-databases.sh`.

Owner notes:
- **Docker memory:** Docker Desktop has 8 GB and the stack uses about 6.5 GB, with Keycloak capped at a 768 MB heap and the gateway at 768 MB. 12 GB or more is recommended so that `make test` builds have headroom.
- **Eval headroom:** the `general` suite passes with at most one case of margin, and two of the three runs after ADR-0032 landed exactly on 0.85. The per-role temperature does not change that, by design — the answer call is still sampled at 0.2. If the margin matters more than variety in phrasing, `temperature.reasoning: 0.0` is a one-line change in `config/models.yaml`.
- **Reranker:** `bge-reranker-v2-m3` is not in the Ollama library, and Ollama has no rerank API. How to serve it is a phase 04 decision (ADR-0008).
- **Open for phase 03:** ADR-0030 (measure a stronger `fast` model on helper-role work) and ADR-0031 (where the `OUTPUT` and `DATA` guards run relative to streaming).

Known gaps (not decisions):
- DATA redaction does not retract; streamed PII is visible until the phase 03 sentence buffer (ADR-0039).

### Fix session: PR #1 review findings (2026-09-13)

Scope: apply the owner's review of PR #1 and nothing else; re-run `make test`, `make eval`, `make smoke`; push.

Checklist:
- [x] A. ADR-0034: an ADR's text is immutable, only its Status line changes; CLAUDE.md rule 10 amended; ADR-0011–0024, ADR-0031 and ADR-0032 restored to their original text; later changes become ADR-0035 and ADR-0036
- [x] 1. Conversation ownership: `gateway.conversations`, 403 `conversation_forbidden` at the gateway, the agent rejects a thread owned by another user or tenant; negative tests on both sides (ADR-0041)
- [x] 2. Capability runtime context: `run_capabilities` passes a `RuntimeContext` (models.call, broker, stream writer, principal, cap_state, resolver); events stream while the capability runs; `_context_for` resolves the tier and assembles a real budget; step overrides accepted; `general` on the same path; a test-only fake capability uses models.call and the broker (ADR-0038)
- [x] 3. `retract` event when validate, OUTPUT or DATA fail after tokens streamed; the web replaces the shown text with the error; `persist_state` stores what the user saw plus the post-guard draft; ADR-0031 superseded by ADR-0039
- [x] 4. Tier caps by principal role (`role:viewer: standard` takes effect), tested. First done by forwarding roles to the agent (ADR-0040); replaced in the same session by the gateway sending `principal.max_tier` (ADR-0046), later bounded by `caps.default` (ADR-0049)
- [x] 5. Audit fail-closed, tested (ADR-0042)
- [x] 6. `make test` runs gateway and web tests every time (no cached test layer) (ADR-0043)
- [x] 7. Capability-neutral answer prompt (Latin ban removed, version bump); no model name in the prompt changelog or `tokens.py`; stale `general/graph.py` docstring fixed (ADR-0037)
- [x] 8. Contract drift tests (agent OpenAPI, events schema, gateway OpenAPI); the documented 422 becomes the actual 400 (ADR-0044)
- [x] 9. Smoke requires ≥3 token events before `done`; `run_evals.py` creates a Langfuse dataset run (ADR-0045)
- [x] Tech debt for phase 02 recorded below
- [x] `make test`, `make eval`, `make smoke` green; push

Session result (2026-09-13):
- **`make up`:** green after a rebuild; Flyway applied `V2 conversations` on the existing database.
- **`make test`:** green. Agent 84 passed. Gateway 42 passed; the test stage ran for real (`--no-cache-filter test`, `cleanTest test --no-build-cache`), including the drift, conversation-ownership and audit-failure tests. Web 47 passed with typecheck and lint.
- **`make smoke`:** green; 9 token events before `done`, block token counts `{"system":58,"user_message":13}`, audit row `ok`.
- **`make eval`:** green, one run on `answer.system@1.3.0`: **18/20 (0.90)** against 0.85. Failures: the currency answer `"…هي الريyal السعودي"` (Latin spliced into an Arabic word; ADR-0033, phase 02) and the sunrise answer, which `validate` stopped as `validation_failed` (now retracted in the UI). Langfuse dataset run `make-eval-20260913T115102Z`.
- API versions: agent and gateway OpenAPI 1.1.0 (ADR-0044); `docs/events.schema.json` gains `retract` (additive, still `events.v1`).

Human review needed (this session):
- `infra/keycloak/realm-export.json`: new `viewer` realm role, assigned to the seed user `viewer` (ADR-0040). An existing Keycloak volume keeps the realm from its first import, so the running dev stack gets the role only after `make clean && make up` or through the admin console; `role:viewer: standard` has no effect there until then.
- `config/prompts/answer/system.ar.md.j2` (1.3.0), `config/messages/errors.yaml` (`output_blocked`, `data_blocked`, `conversation_forbidden`).
- `docker-compose.yml`: `docs/` mounted read-only into `agent-tests`.
- `services/gateway/src/main/resources/db/migration/V2__conversations.sql`.
- README §11 stays as written: roles never leave the gateway; the agent receives `principal.max_tier` (ADR-0046, superseding ADR-0040).
- `config/models.yaml` is now also mounted read-only into the gateway, which reads the `caps.role:*` entries (ADR-0046).

### Fix session: second PR #1 review (2026-09-13)

Scope: apply the second review of PR #1 (violations 1–6, residual risks 1–6 and 8–13); record residual risk 7 as phase 02/08 tech debt; nothing else. Re-run `make test`, `make eval`, `make smoke`; push.

Checklist:
- [x] Capability contract (ADR-0047): the capability's `ContextPackage` is detached from `AgentState` (manifest-needed blocks only, a copy of its own `cap_state` slice, the principal); `RuntimeContext` exposes `call_model(role, step, extra_blocks)` (findings blocks only, assembled through `context.needs`), the broker and `stream_writer`, and no resolver, guard engine or model config; `stream_writer` takes only `CapabilityEvent` (`tool_start`, `tool_end`, `citation`, new `progress`), refused at the type level and at runtime; `CapabilityOutput.cap_state` required; `CapabilityOutput.cost` is non-model cost only, model usage is recorded once by `call_model`; tests for each
- [x] Retract / persist (ADR-0048): history stores the post-guard text, the streamed text goes to `additional_kwargs` for audit; agent `POST /v1/conversations/{id}/turns/{turn_id}/retract` marks the persisted reply retracted and Context Assembly skips it; the gateway calls it when the audit write of a finished turn fails; the web clears streamed text on every error and on `agent_stream_interrupted`; tests
- [x] Tier caps (ADR-0049): effective ceiling = min(`caps.default`, role cap) in the gateway and the agent; `TierResolver.resolve` requires a principal; `principal.max_tier` is an enum of the known tiers and an unknown value is a 400; tests (`role:x: heavy` under `default: standard` → `standard`)
- [x] Ownership (ADR-0050): the agent runner binds an unowned thread to its first caller atomically and refuses another owner before anything is written; the identity node keeps its check; the gateway lower-cases `conversation_id` before binding and forwarding; the agent requires a lower-case UUID; tests
- [x] Gateway JDBC tests (ADR-0051): `JdbcConversationRepository` (`ON CONFLICT` path, concurrent first binds) and the V2 migration against the compose Postgres under the `test` profile, run by `make test`
- [x] `scripts/smoke.sh`: the viewer's turn runs on `standard`; the viewer reusing the analyst's `conversation_id` gets 403 `conversation_forbidden`
- [x] PROGRESS: stale ADR-0040 item fixed; residual risk 7 recorded as tech debt
- [x] `make test`, `make eval`, `make smoke` green; push

Session result (2026-09-13):
- **`make up`:** green after a rebuild of agent, gateway and web; the existing Keycloak volume already carries the `viewer` role.
- **`make test`:** green (exit 0). Agent 111 passed. Gateway 53 passed in the image `test` stage (run for real, ADR-0043) plus 5 JDBC tests in `gateway-db-tests` against the compose Postgres. Web 51 passed with typecheck and lint.
- **`make smoke`:** green; new checks: viewer reusing the analyst's `conversation_id` → 403 `conversation_forbidden`; the viewer's token carries `viewer` and every tier event of the viewer's turn is `standard`. The general floor is itself `standard`, so this check cannot tell a working cap from a missing one; the cap arithmetic is covered by `TierCapsTest` and `tests/unit/test_models.py`.
- **`make eval`:** green, **18/20 (0.90)** against 0.85. Failures unchanged: `"…هي الريyal السعودي"` (ADR-0033, phase 02) and the sunrise answer stopped as `validation_failed`. Langfuse dataset run `make-eval-20260913T125148Z`.
- API versions: agent OpenAPI stays 1.1.0 (ADR-0046's reasoning: 1.1.0 has not left this PR) despite the retract endpoint, the `max_tier` enum and the UUID `conversation_id`; `docs/events.schema.json` gains `progress` (additive, still `events.v1`); gateway OpenAPI unchanged.

Human review needed (this session):
- `docker-compose.yml`: `gateway-db-tests` under the `test` profile connects as the `postgres` superuser (`POSTGRES_PASSWORD`) to create and drop a throwaway database; the `gateway-gradle-cache` volume is now declared in compose.
- `config/messages/errors.yaml`: new `turn_not_found` text (ar/en).
- `Makefile`: `make test` now also starts `postgres` and runs `test-gateway-db`.
- The agent API version decision above (1.1.0 kept) is the owner's call to confirm.

How to verify:
- `make up && make test && make smoke && make eval`
- Gateway JDBC tests alone: `make test-gateway-db`

### Fix session: third PR #1 review (2026-09-13)

Scope: the single finding of the third review of PR #1 (a capability subgraph inherited the main graph's LangGraph config, so `get_config()` reached the checkpointer and another conversation's thread); nothing else. Re-run `make test`, `make smoke`; push.

Checklist:
- [x] `run_capabilities` invokes the capability subgraph with a fresh config: no checkpointer, no parent `configurable`, no inherited callbacks or store; only the `RuntimeContext`. Unit test: inside the subgraph `get_config()` holds no checkpointer and no parent `thread_id`
- [x] Integration test, victim and attacker in two conversations: a capability that calls `get_config()` and `aget_tuple` on another `thread_id` obtains no state; kept as the regression test for phase 02's Postgres checkpointer
- [x] Lint rule: code under `services/agent/app/capabilities/` must not import `langgraph.config.get_config`, `get_stream_writer`, `get_store` or any checkpointer class; runs in `make test`
- [x] ADR-0052; ADR-0047's enforcement caveat marked superseded
- [x] `make test`, `make smoke` green; push

Session result (2026-09-13):
- **Fix:** `_invoke_isolated` in `services/agent/app/graph/nodes/capabilities.py` runs the subgraph in an asyncio task whose context clears `var_child_runnable_config`. A capability graph compiled with its own checkpointer or store ends the turn as `turn_failed`.
- **Tests:** the new integration test failed before the fix (the attacker's capability read the victim's thread, message included) and passes after it. The new unit tests (fresh config under a main graph with checkpointer, store, thread id and callback handler; own checkpointer or store refused) failed on the pre-fix node and pass on the fixed one. `tests/unit/test_capability_lint.py` proves each ban fires under `app/capabilities/` only and lints that tree.
- LangGraph always sets `__pregel_checkpointer` in a top-level run's `configurable` (value `None` without a checkpointer), so the tests assert it holds no saver rather than that the key is absent (ADR-0052).
- **`make test`:** green (exit 0). Agent 124 passed (111 + 13 new). Gateway image `test` task and `gateway-db-tests` `dbTest` task both executed, BUILD SUCCESSFUL. Web 51 passed.
- **`make up`:** green, agent rebuilt. **`make smoke`:** green, all phase 01 checks.
- `make eval` not re-run this session, by instruction; no prompt, model or answer-path change.

Left open (ADR-0052 consequences, not fixed by instruction):
- `RuntimeContext`'s callables are core objects: plain attribute access still reaches the turn's services (`runtime.context.broker._ctx.services.graph`, `call_model.__closure__`), and from there any thread. Neither the fresh config nor the lint rule stops it.
- Phase 08: a capability subgraph can no longer `interrupt()` the main graph; the HITL interrupt must be raised by the core.

Human review needed (this session):
- `services/agent/app/capabilities/ruff.toml`: the banned-api list.

How to verify:
- `make up && make test && make smoke`
- Isolation tests alone: `cd services/agent && uv run pytest tests/integration/test_capability_isolation.py tests/unit/test_capability_runtime.py tests/unit/test_capability_lint.py` (mock LiteLLM running)

### Fix session: fourth PR #1 review (2026-09-13)

Scope: the two findings of the fourth review — the fresh config (ADR-0052) stops a capability from *reading* the parent config/checkpointer/store, but not from *escaping* to the main graph through LangGraph control-flow. A capability node that raised `ParentCommand` wrote forged identity/permissions/`cap_state`/messages into the main graph's checkpoint; a forged `GraphInterrupt` left the thread with a pending interrupt. The registry also accepted any object with an `ainvoke`. Fix by an exception/return firewall in the core, type-checked compilation, and lint. Nothing else. Re-run `make test`, `make smoke`; push.

Checklist:
- [x] 1. Exception firewall in `_invoke_isolated`: the subgraph runs in the isolated task; ANY `BaseException` other than a clean return is converted to a `capability_failed` result and recorded on the capability span; `CancelledError` is re-raised only after the subgraph task is cancelled and drained. ParentCommand, GraphInterrupt, a returned `Command` (raised as ParentCommand by LangGraph), timeout and unknown exceptions all caught. Nothing a capability raises reaches the main graph's retry handler or its checkpoint.
- [x] 2. Registry accepts only a `CompiledStateGraph` the core compiled from the capability's `StateGraph`: `build()` returns a `StateGraph`, the core compiles it with no checkpointer, store or `interrupt_before/after`; any other return type is a `ManifestError`.
- [x] 3. Lint (`app/capabilities/ruff.toml`): ban `langgraph.errors`, `langgraph.types.Command`, `langgraph.types.interrupt`, and the `langgraph.pregel` and `langgraph.checkpoint` packages under `app/capabilities/`.
- [x] 4. Tests: ParentCommand reproduction (identity, permissions, other namespaces and messages unchanged) and forged-GraphInterrupt reproduction (no pending interrupt on the thread) as permanent integration tests; a unit test that an arbitrary exception yields `capability_failed` and an untouched checkpoint; a registry test that a non-`StateGraph` `build()` is rejected; new lint bans covered.
- [x] 5. ADR-0053: capability isolation is one exception/return firewall in the core, plus type-checked compilation, plus lint; enumerated bans are secondary; process-level isolation (separate worker) is the phase-09 option if capabilities ever become third-party.
- [x] `capability_failed` error code added to `config/messages/errors.yaml`, the web i18n catalogs and `KNOWN_ERROR_CODES`.
- [x] `make test`, `make smoke` green; push. (`make eval` not re-run: no prompt, model or answer-path change.)

Session result (2026-09-13):
- **Fix (finding 1).** `_invoke_isolated` (`app/graph/nodes/capabilities.py`) now awaits the subgraph task behind a firewall: `TimeoutError` → `CapabilityFailed(retryable)`; `CancelledError` → task cancelled and drained, then re-raised; any other `BaseException` (ParentCommand, GraphInterrupt, a returned Command, unknown) → `CapabilityFailed`. `run_capabilities` records the failure on the `capability.{slug}` span and ends the turn as the new `capability_failed` error. `_output`'s invalid-output path and the own-checkpointer/store check now also raise `CapabilityFailed`, so every capability failure ends the turn as `capability_failed` (was `turn_failed`).
- **Fix (finding 2, part a).** `build()` returns an uncompiled `StateGraph`; `registry._compile_capability` compiles it in the core with no checkpointer, store or interrupts, and rejects any other return type as `ManifestError`. `general/graph.py` and the test `probe` capability return `StateGraph`; `LoadedCapability.graph` is typed `CompiledStateGraph`.
- **Fix (finding 2, part b).** `app/capabilities/ruff.toml` bans `langgraph.errors`, `langgraph.types.Command`, `langgraph.types.interrupt`, `langgraph.pregel` and `langgraph.checkpoint` under `app/capabilities/`.
- **Tests.** `tests/integration/test_capability_isolation.py`: a ParentCommand from a capability leaves identity/permissions/other namespaces/messages unchanged and the turn ends `capability_failed`; a forged GraphInterrupt leaves the thread with no pending interrupt. `tests/unit/test_capability_runtime.py`: an arbitrary exception yields `capability_failed` and an untouched checkpoint (no partial writes, no interrupt). `tests/unit/test_registry.py`: `_compile_capability` compiles a `StateGraph` and rejects a compiled graph, a fake graph, a plain object and `None`. `tests/unit/test_capability_lint.py`: the five new bans fire under `app/capabilities/`. The three existing capability-failure assertions moved from `turn_failed` to `capability_failed`.
- **`make test`:** green (exit 0). Agent 139 passed. Gateway BUILD SUCCESSFUL (image `test` task, run for real) plus 5 `gateway-db-tests`. Web 51 passed with typecheck and lint.
- **`make up`:** green (agent rebuilt). **`make smoke`:** green (exit 0), all phase 01 checks.
- `make eval` not re-run, by instruction: no prompt, model or answer-path change.
- ADR-0053 records the firewall + type-checked compilation + lint, and notes process-level isolation as the phase-09 option for third-party capabilities.

Human review needed (this session):
- `services/agent/app/capabilities/ruff.toml`: the five added bans.
- README §18.1/§18.3 now describe `build()` as returning a compiled subgraph; ADR-0053 moves compilation into the core (`build()` returns a `StateGraph`). README text left unchanged this session; the owner may want to patch those two lines to match, like ADR-0026 did.
- `config/messages/errors.yaml`, `services/web/src/i18n/{ar,en}.json`, `services/web/src/lib/errors.ts`: the new `capability_failed` code and its ar/en text.

## Phase 02: State, Context Assembly, Memory

- Branch: `phase/02-context-state-memory`
- Status: not started
- Done when: `AgentState` checkpointed; assembler pipeline with budget logging and `NeedsMore`; summarizer kicks in past 8k tokens; memory write/retrieve; `/settings/memory`; `/v1/debug/context`.

Tech debt carried from the PR #1 review (recorded 2026-09-13; not fixed in phase 01, by instruction):
- [ ] Replace snapshot tests with behaviour tests. These assert config and prompt values and are edited in lockstep with every config change: `services/agent/app/capabilities/general/tests/test_general.py::test_manifest_is_valid`, `services/agent/tests/unit/test_models.py` (`test_repo_models_config_pins_helper_roles_to_fast`, `test_repo_models_config_makes_helper_roles_deterministic`), `services/agent/tests/unit/test_prompts.py::test_answer_prompt_renders_arabic_without_optional_sections`, and the `prompt_refs` version assertions in `services/agent/tests/unit/test_assembler.py`.
- [ ] Cover the untested paths:
  - `validation_failed`, `context_overflow` and `model_unavailable` through the API (`POST /v1/turns`), not only at node level.
  - A capability that exceeds `limits.timeout_s` (`app/graph/nodes/capabilities.py`).
  - `cap_state` isolation between two capabilities in one turn.
  - Client disconnect mid-turn (`app/api/runner.py` task cancellation).
- [ ] Add the lint rule README §18.4 requires: a capability must not read or write another capability's `cap_state` namespace. Today only the runtime slice exists.

Tech debt carried from the second PR #1 review (recorded 2026-09-13; not fixed in phase 01, by instruction):
- [ ] Residual risk 7: `GET /v1/debug/context/{turn_id}` (phase 02) and `POST /v1/turns/{id}/approve` (phase 08) must each perform the conversation ownership check themselves. Both reach a thread without running `load_identity_and_state` (a debug read, and a resume from an interrupt in the middle of the graph), and neither goes through the turn runner's binding (ADR-0050).
- [ ] `ConversationOwners` (ADR-0050) is in memory, like the phase 01 checkpointer; make it durable in Postgres together with the checkpointer, so a restart keeps both.

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

Hardening option carried from the fourth PR #1 review (ADR-0053): capability isolation is enforced in-process (fresh config, exception/return firewall, core-owned compilation, lint). `RuntimeContext`'s callables are still core objects reachable by attribute reflection. If capabilities ever become third-party rather than first-party code in this repo, run each subgraph in a separate worker process with only its `RuntimeContext` marshalled across the boundary. Not needed for v1's five first-party capabilities.
