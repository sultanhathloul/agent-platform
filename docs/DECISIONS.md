# Architecture Decision Log

Append-only. Never rewrite or delete a past entry. To change a decision, add a new entry that supersedes it and reference the old one by number.

Closed decisions in `README.md §2` and `§18–§20` are not repeated here. They are the baseline, and entries below refine or extend them.

Entry format:

```
### ADR-NNNN: <title>
- Date: YYYY-MM-DD
- Phase: NN
- Status: decided | assumed | proposed | superseded by ADR-NNNN
- Context: <what forced a choice>
- Decision: <what we chose>
- Consequences: <what it costs, what it enables, what to revisit>
```

Entries are in number order. An entry's text never changes once committed; its Status line carries its state and may only gain a one-line note, `Accepted on <date>` or `Superseded by ADR-NNNN on <date>` (ADR-0034). `decided` entries were confirmed by the owner; `assumed` entries fill a gap in the spec with the simplest option that fits the architecture; `proposed` entries argue against a closed decision, which stays in force until the owner accepts the proposal.

ADR numbers 0006–0010 follow the owner's decision numbering (decisions 6–10); 0001–0005 are unassigned.

### ADR-0006: Permission naming is `cap:{slug}` and `kb:{slug}:read`
- Date: 2026-09-10
- Phase: 01
- Status: decided
- Context: README §5.1 and §11 named permissions `tool:sql`, `tool:web`, `tool:docs`, while §5.3 and §18.2 gate capabilities with `cap:{slug}`. Two names for one concept.
- Decision: A capability is gated by the realm role named in its manifest `permission`, always `cap:{slug}`. A knowledge base is gated by `kb:{slug}:read`. `cap:general` is a composite child of the base `user` role, so every user has it. The `tool:*` permission naming is dropped everywhere. README §5.1 and §11 are patched to match.
- Consequences: The gateway puts only `cap:*` and `kb:*:read` roles into `permissions[]`. Tools inside a capability are authorized by the capability's permission plus `PRE_TOOL` policies, never by per-tool roles. Open point: `tool:{name}` also appears as a guardrail policy *scope* (§10.1, §18.1, §18.4). That is not a permission and was left unpatched. It must be settled before phase 03.

### ADR-0007: `make test` uses LiteLLM with mock responses
- Date: 2026-09-10
- Phase: 01
- Status: decided
- Context: Tests must be deterministic and offline, but rule 6 forbids bypassing Context Assembly or LiteLLM, even in tests.
- Decision: `make test` sends every model call down the production path (Context Assembly → `models.call` → LiteLLM proxy) to a LiteLLM instance whose aliases return `mock_response`. Real models are used only in `make eval` and `make smoke`. No test may call a model endpoint directly.
- Consequences: `make test` needs Docker for the mock LiteLLM container. Tests show that the pipeline works, not that the answers are good; model quality is covered only by evals.

### ADR-0008: Development model defaults
- Date: 2026-09-10
- Phase: 01
- Status: decided
- Context: README §2 lists dev defaults and allows "the largest model the host can run" for `heavy`.
- Decision: `fast` = `qwen2.5:3b`, `standard` = `qwen2.5:14b-instruct`, `heavy` = `gemma4:31b` (already on the development host; tiers may mix model families), `embed` = `bge-m3`, `rerank` = `bge-reranker-v2-m3`. The only mapping lives in `infra/litellm/config.yaml`. Python tooling is `uv`.
- Consequences: `heavy` is a different model family from the other tiers, so prompts must not rely on Qwen-specific formatting. `bge-reranker-v2-m3` is not in the Ollama library, and Ollama has no rerank API. How it is served is decided in phase 04, the first phase that reranks.

### ADR-0009: Phase 01 scope
- Date: 2026-09-10
- Phase: 01
- Status: decided
- Context: §15 phase 1 is short. Four scope questions were settled with the owner.
- Decision: (1) A minimal assembler sits behind the real `ContextAssembler` interface and emits per-block token counts. The full pipeline is phase 02. (2) Compose includes ClickHouse and the OTel collector from phase 01. (3) `scripts/smoke.sh` grows each phase: each phase adds checks and none are removed. (4) Guardrails have seven checkpoints: the six in §10.1 plus `MODEL_TIER` (§19.3).
- Consequences: Phase 01 defines all seven checkpoints as pass-through hook points (graph nodes, broker, tier resolver). Phase 03 fills in the engine without moving them.

### ADR-0010: Repository layout and service boundaries
- Date: 2026-09-10
- Phase: 01
- Status: decided
- Context: README §4 and §9 placed capability code under `tools/<name>/` and prompts under `models/prompts/`, which contradicts §18 and CLAUDE.md. §2 also left a FastAPI gateway open as an option.
- Decision: Follow §18 and CLAUDE.md. Capability code lives only in `services/agent/app/capabilities/<slug>/`. `app/rag/` is a shared core library that capabilities import. Prompts live only in `config/prompts/` and `capabilities/*/prompts/`; `models/` holds a loader, not prompts. The gateway is Spring Boot 3 on Java 21; the FastAPI-gateway option is closed. `tenant_id` comes from the Keycloak user attribute `tenant_id`, mapped into the token, and defaults to `default` when absent.
- Consequences: README §4, §9 and §11 are patched to match. Keycloak's declarative user profile must declare `tenant_id` as an admin-managed attribute.

### ADR-0011: Browser login with oidc-client-ts; the browser calls the gateway directly
- Date: 2026-09-10
- Phase: 01
- Status: assumed — Accepted on 2026-09-13
- Context: §13 allows next-auth or oidc-client-ts. The `web` client is public with PKCE (§11). Inside the web container, the browser-facing Keycloak URL is unreachable.
- Decision: oidc-client-ts runs in the browser (authorization code + PKCE S256). Tokens stay in sessionStorage and renew with the refresh token. The browser calls the gateway directly under CORS, so no Next.js server sits in the SSE path. URLs are read at request time by the root layout, so one image works for any deployment.
- Consequences: No server-side session, and the gateway is the only token consumer. The gateway must keep CORS limited to the web origin. XSS would expose the tokens in sessionStorage, so a CSP should follow (phase 09 hardening).

### ADR-0012: Token issuer is the browser-facing URL; JWKS is fetched internally
- Date: 2026-09-10
- Phase: 01
- Status: assumed — Accepted on 2026-09-13
- Context: Tokens are issued to the browser at `http://localhost:8081`, but containers reach Keycloak at `http://keycloak:8080`.
- Decision: Keycloak runs with `KC_HOSTNAME=<public URL>` and `KC_HOSTNAME_BACKCHANNEL_DYNAMIC=true`. The gateway validates `iss` against the public issuer string and fetches keys from the internal JWKS URI. It never uses issuer discovery. Audience must contain `gateway` (an audience mapper on the `web` client adds it).
- Consequences: Two settings (`GATEWAY_KEYCLOAK_ISSUER`, `GATEWAY_KEYCLOAK_JWKS_URI`) must agree with the Keycloak hostname.

### ADR-0013: Gateway runtime, SSE proxy and audit storage
- Date: 2026-09-10
- Phase: 01
- Status: assumed — Accepted on 2026-09-13; Superseded by ADR-0042 on 2026-09-13 (audit write timing only)
- Context: The gateway must stream agent SSE unchanged (§6.1.6), keep one trace per turn (§12), and write one audit record per turn (§16).
- Decision: Spring MVC runs on Java 21 virtual threads, and the proxy runs on the request thread, so the trace context carries into the agent call and `traceparent` propagates. The upstream is a JDK HttpClient (HTTP/1.1) that forwards lines unchanged and flushes at each event boundary. A mid-stream failure appends one `error` event (`agent_stream_interrupted`, `seq: -1`). Audit lives in schema `gateway` of database `app` (Flyway). A trigger rejects UPDATE, DELETE and TRUNCATE. Statuses are `ok`, `error`, `denied`, `aborted`; requests rejected by validation are not audited.
- Consequences: The `app` role owns the table and could drop the trigger. True immutability needs a separate insert-only role (phase 09). `/v3/api-docs` is public in compose; disable it with `SPRINGDOC_API_DOCS_ENABLED=false` in production.

### ADR-0014: `general` contributes no findings; the answer node makes the only model call
- Date: 2026-09-10
- Phase: 01
- Status: assumed — Accepted on 2026-09-13
- Context: UC5 is "direct model answer, no tools", and every capability must run through the §18 contract. If both `general` and the answer node called the model, general turns would cost two calls.
- Decision: The `general` subgraph returns an empty finding and makes no model call. The core `answer` node answers, on the tier floor of the selected capabilities. When a call is made on behalf of capabilities, the highest capability floor (with per-step overrides) replaces the role's provisional default. Other calls use the role default (§19.1). The answer budget is capped by the largest `max_input_tokens` of the selected capabilities.
- Consequences: General questions use one `fast` model call. The same path serves turns where several capabilities contribute evidence.

### ADR-0015: Routing rules gain `fallback`; the router model arrives with the second capability
- Date: 2026-09-10
- Phase: 01
- Status: assumed — Accepted on 2026-09-13
- Context: §18.2 lists `toggled` and `keyword` rules. With one capability, a router-model call would decide nothing.
- Decision: The manifest rules are `toggled`, `keyword` (matched after Arabic normalization), `attachments` and `fallback`. `fallback` capabilities are chosen only when no other rule matched; `general` declares it. `toggled` matches nothing until the turn request carries capability toggles (phase 05). The 3B router model is added in phase 04, the first phase with two capabilities. Arabic normalization lives in `app/text/arabic.py` so the router, the `keyword_list` checker and chunkers can share it.
- Consequences: General turns cost no router call. Phase 04 adds a router step for when rules leave several candidates.

### ADR-0016: AgentState additions and in-memory checkpointing in phase 01
- Date: 2026-09-10
- Phase: 01
- Status: assumed — Accepted on 2026-09-13
- Context: §5.1 has no place for per-turn ids, locale, capability outputs or capability state namespaces (§18.2).
- Decision: Four fields are added:
  - `turn_id`: used in event envelopes and, from phase 02, the debug context lookup.
  - `locale`: selects the prompt locale.
  - `capability_outputs`: read by `answer` and `validate`.
  - `cap_state`: a dict keyed by `manifest.state_namespace`. `run_capabilities` hands each capability only its own slice.
  `persist_state` appends the finished user/assistant exchange to `messages`, so the window never contains the current message. Phase 01 uses LangGraph's `InMemorySaver`; the Postgres checkpointer replaces it in phase 02.
- Consequences: Conversation history is lost when the agent restarts until phase 02.

### ADR-0017: `models.call` takes a ContextPackage and a TierDecision
- Date: 2026-09-10
- Phase: 01
- Status: assumed — Accepted on 2026-09-13
- Context: Rule 6 requires every model call to go through Context Assembly. §19.2 shows `models.call(role=..., step=..., messages=...)`.
- Decision: The gateway accepts only an assembled `ContextPackage` plus the resolver's `TierDecision`, and it rejects a package assembled for a different tier. The call flow is resolve tier → budget → assemble → call. Calls always stream; usage comes from LiteLLM (`stream_options.include_usage`), estimated only when it is missing.
- Consequences: Raw messages cannot reach a model by construction. The resolve/assemble/call sequence is explicit at each call site.

### ADR-0018: Token counting with tiktoken o200k_base, baked into the image
- Date: 2026-09-10
- Phase: 01
- Status: assumed — Accepted on 2026-09-13
- Context: Budgets need token counts for Qwen and Gemma, and the runtime must work offline.
- Decision: `TiktokenCounter("o200k_base")` is a model-agnostic estimate, behind a `TokenCounter` protocol. The encoding file is downloaded at image build (`TIKTOKEN_CACHE_DIR`). Billing-grade counts come from model usage.
- Consequences: Budgets are estimates, and the tier budget keeps headroom (`reserved_loop_headroom_tokens`). An exact per-model tokenizer can replace it behind the same protocol.

### ADR-0019: Test harness layout
- Date: 2026-09-10
- Phase: 01
- Status: assumed — Accepted on 2026-09-13; Superseded by ADR-0043 on 2026-09-13 (gateway and web test caching only)
- Context: ADR-0007 requires tests to go through LiteLLM, and the host has no JDK 21.
- Decision: `litellm-mock` (same image, `infra/litellm/config.mock.yaml`) and `agent-tests` run under the compose `test` profile. Gateway and web tests run in the `test` target of their Dockerfiles. Locally, `uv run pytest` reaches the mock on port 4001. Integration tests fail, never skip, when the mock is unreachable. A test tracer provider with an in-memory exporter asserts span attributes, including the context block token counts.
- Consequences: `make test` needs Docker only; no host JDK or Node is required.

### ADR-0020: Configuration files added in phase 01
- Date: 2026-09-10
- Phase: 01
- Status: assumed — Accepted on 2026-09-13
- Context: Several phase 01 behaviours need data that §20 says belongs in files.
- Decision:
  - `config/context.yaml` holds the Context Assembly pipeline order, window size, headroom and per-node needs/system prompt.
  - `config/messages/errors.yaml` holds agent error texts (ar/en). The gateway's texts live in `messages_{ar,en}.properties`, and the web renders its own `errors.<code>` strings, falling back to the server message.
  - A capability that needs the `system` block must ship a `<slug>.system` prompt. This is checked at startup.
- Consequences: Adding a context step or a node is a config change plus one class.

### ADR-0021: Observability defaults
- Date: 2026-09-10
- Phase: 01
- Status: assumed — Accepted on 2026-09-13
- Context: §12 hashes user ids but says nothing about prompt content in traces.
- Decision:
  - `OBSERVABILITY_CAPTURE_CONTENT=true` by default: generation spans carry messages and output, and the trace carries the user message and the answer.
  - User ids are HMAC-SHA256 hashed with `USER_ID_HASH_SALT`.
  - The gateway excludes probes and API docs from traces, and turns off Spring Security observations.
  - The `context.assemble` span carries `context.block.<name>.tokens` plus JSON metadata `block_tokens` and `context`.
  - Langfuse, LiteLLM and Next.js telemetry are off.
- Consequences: Traces contain user text. Set capture to `false` where Langfuse must not hold content.

### ADR-0022: Infra details the spec left open
- Date: 2026-09-10
- Phase: 01
- Status: assumed — Accepted on 2026-09-13
- Context: §14 names services, not their wiring.
- Decision:
  - **Postgres:** one owner role per database (`app`, `business`, `keycloak`, `langfuse`).
  - **MinIO:** bucket `kb` became `kb-docs`, because S3 bucket names need at least 3 characters.
  - **LiteLLM:** runs without a database. `heavy → standard` is its only fallback, because a fallback below `standard` would break the risk floor.
  - **Ollama:** `num_ctx` is set per alias so prompts are not silently truncated; the dev `heavy` window is 65536.
  - **Keycloak:** runs `start --import-realm` (production mode, HTTP enabled), and the realm file uses `${ENV}` placeholders for the web URL and the gateway client secret.
  - **Langfuse:** uses the MinIO root credentials.
  - **Login in tests:** `scripts/get_token.py` performs the real PKCE browser flow for smoke and eval, so the realm has no direct-grant client.
- Consequences: Human review needed: the realm export (service-account `realm-management` roles), the LiteLLM aliases and `num_ctx` values, and MinIO credential scoping for Langfuse (a dedicated key is better, phase 09).

### ADR-0023: Empty state digest omitted; short answer prompt
- Date: 2026-09-10
- Phase: 01
- Status: assumed — Accepted on 2026-09-13
- Context: §7 includes the state digest "always". On a first turn, the digest can only say "Turn: 1". Measured through LiteLLM on the fast tier against the `general` suite (8 cases):
  - `answer.system@1.0.0` plus that digest: 2/8.
  - The long prompt's rule "if the user writes in Arabic, answer in Modern Standard Arabic" made the fast model answer English questions in Arabic, even when called directly on Ollama.
  - A short prompt with the digest: 5/8. The short prompt alone: 6/8.
- Decision:
  - The digest step emits a block only when there is something to report (selected KBs, decisions, open threads, artifacts). It no longer includes the turn number, which §7 does not list.
  - `answer.system@1.1.0` is a short instruction set. The permitted tool list renders only when the node has tools; evidence and policy-hint sections render only when present.
- Consequences: A first-turn context holds the system and user blocks only. The digest reappears on its own once phase 02 adds decisions and open threads. Prompt changes keep being measured against the eval suites (§20).

### ADR-0024: The fast tier misses the Arabic general-knowledge eval
- Date: 2026-09-10
- Phase: 01
- Status: proposed — Superseded by ADR-0035 on 2026-09-13
- Context: README §19.4 puts `general` on `min_tier: fast`, and ADR-0008 maps `fast` to `qwen2.5:3b`. The `general` suite (5 Arabic and 3 English cases; `contains_any`, `excludes_any` and script checks) has a 0.85 threshold. Measured through LiteLLM with `answer.system@1.1.0`:
  - `fast`: 6/8 and 5/8 on two probe runs, and 5/8 (0.62) in `make eval` through the full stack.
  - `standard`: 7/8 (probe).
  The failures on `fast` are wrong Arabic facts (flag colour, largest ocean) and an occasional reply in Chinese. Prompt fixes (ADR-0023) closed most of the gap; what remains is model capacity.
- Decision (proposed, not in force). One of:
  - (a) **Recommended.** Replace the `fast` model with a stronger small Arabic-capable model, keeping §19.4. Every helper role (router, summarizer, judge, extractor) also runs on `fast`, and the `llm_judge` checker is the primary Arabic topic filter (§10.2), so they would all gain too. The candidate would need to be pulled (with owner approval) and measured on this suite first.
  - (b) Set `general.min_tier: standard`, overriding §19.4. Each general turn gets slower (not yet measured).
  - (c) Keep both, and lower the `general` threshold to what `fast` achieves, accepting weaker general answers.
- Consequences: Until the owner decides, `general` stays on `fast`, the threshold stays at 0.85, and `make eval` fails. Phase 01 cannot be marked done.

### ADR-0025: `tool:{name}` stays as a guardrail policy scope
- Date: 2026-09-13
- Phase: 01 (closes the open point in ADR-0006, raised for phase 03)
- Status: decided
- Context: ADR-0006 dropped `tool:*` as a *permission* name but left `tool:{name}` standing as a policy **scope** in §10.1, §18.1 and §18.4. One name doing two jobs is what caused the original confusion, so the owner settled which job it keeps.
- Decision: `tool:{name}` stays, and it means one thing only: *which tool or capability a policy applies to*. It is never a permission and never a Keycloak role. Authorization stays with `cap:{slug}` and `kb:{slug}:read` (ADR-0006). README §10.1 states the distinction next to the scope list.
- Consequences: Phase 03 policy rows carry `scope ∈ {global, tenant:{id}, role:{role}, kb:{slug}, tool:{name}}`, narrower overriding wider. A capability's seed policies under `capabilities/*/policies/` use scope `tool:{slug}`. Nothing in the gateway ever creates or reads a role named `tool:*`.

### ADR-0026: README states seven checkpoints
- Date: 2026-09-13
- Phase: 01
- Status: decided
- Context: ADR-0009 settled seven checkpoints (the six of §10.1 plus `MODEL_TIER` from §19.3), and the `Checkpoint` enum in code has seven. README §0, §10.1, §15 and §18.4 still said six.
- Decision: Patch those four places to seven and add the `MODEL_TIER` row to the §10.1 table (sees the tier resolver's request; applies ceilings by role, tenant or quota). Minimal edit; no other text changes.
- Consequences: Documentation matches `app/guardrails/checkpoints.py`, which `tests/unit/test_broker_and_guard.py::test_seven_checkpoints` pins.

### ADR-0027: `validate` rejects an Arabic answer that contains CJK characters
- Date: 2026-09-13
- Phase: 01
- Status: decided — Superseded by ADR-0060 on 2026-09-15 (the CJK-only check)
- Context: The drift into Chinese measured in ADR-0024 is a wrong answer, not a phrasing problem. The eval suite's `script` check catches it offline, but at runtime nothing stopped such an answer from reaching the user.
- Decision: `app/text/script.py` provides `contains_cjk` (CJK ideographs and extension A, compatibility ideographs, kana, hangul). `validation_issues` takes the turn `locale` and adds the issue `cjk_in_arabic_answer` when `locale == "ar"` and the answer holds a CJK character. Deterministic, no model call. Only the Arabic locale is checked, because an English or mixed answer may legitimately quote CJK. Covered by `tests/unit/test_validate.py`.
- Consequences: Such a turn ends as `validation_failed` instead of showing mixed-script text. From phase 08 this failure is what escalates a tier and retries once, which is the right response to script drift. Other locales need their own rule when they are added.

### ADR-0028: The `general` suite is 20 cases; the threshold stays 0.85
- Date: 2026-09-13
- Phase: 01
- Status: decided
- Context: With 8 cases, one miss cost 12.5 points and the suite could not tell a model regression from one unlucky question.
- Decision: Grow the suite to 20 cases — 13 Arabic and 7 English — and keep the threshold at 0.85, so three misses are tolerated. Checks stay deterministic: `contains_any` and `excludes_any` compared after Arabic folding, plus the `script` share.
- Consequences: `make eval` runs 20 turns through the live stack on `standard`, so it takes longer. New cases must have a ground truth that does not age (no "current" office-holders, no dated figures) and tolerant `contains_any` lists covering ordinary Arabic spelling variants.

### ADR-0029: First-run documentation, `make models`, and remote model endpoints
- Date: 2026-09-13
- Phase: 01
- Status: assumed — Superseded by ADR-0066 on 2026-10-05 (where models are named and what the host scripts read only)
- Context: README explained the architecture but not how to start from an empty machine, and every model endpoint was assumed to be Ollama on the developer's own host.
- Decision:
  - README §21 documents prerequisites, the exact first-run commands, URLs and seed users, and a subsection on pointing LiteLLM at a shared model server.
  - `scripts/pull_models.sh` (target `make models`) pulls the models named in `infra/litellm/config.yaml` — still the only file that names a model — through the Ollama HTTP API, so `OLLAMA_HOST` can point it at a remote server. Present models are skipped, so it is re-runnable.
  - `VLLM_API_KEY` is added to `.env.example` and passed to the `litellm` container, so moving a tier to an OpenAI-compatible endpoint is two `.env` values plus three lines in the LiteLLM config.
- Consequences: The host-side scripts (`preflight.sh`, `pull_models.sh`) read shell variables, not `.env`, because they run outside compose; §21.4 says so explicitly. `make models` needs `curl` and `jq`, which `preflight.sh` already assumes.

### ADR-0030: Evaluate `qwen2.5:7b-instruct` as the `fast` model, on helper-role work
- Date: 2026-09-13
- Phase: 03 (proposed by the owner while deciding ADR-0024)
- Status: proposed — Superseded by ADR-0065 on 2026-09-16
- Context: ADR-0024 rejected `fast` for general knowledge but did not test what `fast` is actually for. Every helper role runs there — router, summarizer, judge, extractor — and the `llm_judge` checker is the primary Arabic topic filter (§10.2), so the quality of `fast` decides the quality of guardrails, routing and memory extraction. `qwen2.5:3b` was only ever measured on general knowledge, the one job it is not meant to do.
- Proposal: In phase 03, measure `qwen2.5:7b-instruct` against `qwen2.5:3b` as the `fast` alias, using a **helper-role suite** — Arabic classification, Arabic summarization, and judge decisions with known verdicts — not the general-knowledge suite. Decide on that evidence. Pulling the model needs owner approval (ADR-0008 fixes the dev defaults).
- Consequences: If it wins, guard judging and routing improve for the cost of extra memory resident on the model host; if it does not, `fast` stays at 3B with evidence behind it. `general` stays on `standard` either way (ADR-0024).

### ADR-0031: Where the `OUTPUT` and `DATA` guards run relative to streaming
- Date: 2026-09-13
- Phase: 03 (proposed by the owner while closing phase 01)
- Status: proposed — Superseded by ADR-0039 on 2026-09-13
- Context: Tokens stream to the browser as the model produces them, but the `OUTPUT` and `DATA` checkpoints only see a finished draft (§5.2). A late `block` or `redact` would have to retract text the user has already read.
- Proposal: Split the two checkpoints by what they can safely do mid-stream. `DATA` (PII redaction) runs on a **sentence-level buffer** during streaming: a sentence is released only once it is clean, so redaction happens before the text leaves. `OUTPUT` **block** policies run on the **complete answer before streaming starts**, because a block is not something a buffer can undo. Measure the added time-to-first-token for both and record the numbers with the decision.
- Consequences: A turn with `OUTPUT` block policies enabled pays roughly one full generation before the first token appears, which is the trade-off phase 03 has to weigh; with only `DATA` policies the cost is one sentence of latency. The measurement is part of the phase 03 done-check.

### ADR-0032: Pin the decoding temperature for the answer call
- Date: 2026-09-13
- Phase: 01, proposed for phase 02
- Status: proposed — Superseded by ADR-0036 on 2026-09-13
- Context: `ModelGateway.call` samples at `temperature=0.2`, a value chosen while building phase 01 and never recorded. With `answer.system@1.2.0` the `general` suite was measured three times in a row on `standard`: 18/20, 17/20, 18/20 (0.90, 0.85, 0.90). One run landed exactly on the threshold. Both recurring failures are script drift by `qwen2.5:14b-instruct` — `"العملة … هي الريyal السعودي"` and an answer that continues in Chinese — not knowledge errors, and they move between runs because decoding is sampled.
- Proposal: give each role a decoding temperature in `config/models.yaml`, where §20 keeps tuning knobs, and set the `reasoning` role to 0. A factual government assistant gains nothing from sampling, and an eval suite that gates a phase should not be a coin flip when it sits near its threshold.
- Consequences: Answers become reproducible for a fixed prompt and context, so an eval regression is attributable to a change rather than to luck; phrasing becomes more uniform. This changes behaviour for every capability, so it is the owner's call rather than an assumption. Until it is decided, the suite passes with about one case of headroom.

### ADR-0033: `validate` gains a word-level mixed-script rule in phase 02
- Date: 2026-09-13
- Phase: 02 (decided in phase 01, deliberately not implemented yet)
- Status: decided — Superseded by ADR-0060 on 2026-09-15
- Context: The CJK check (ADR-0027) catches an Arabic answer that drifts into Chinese, Japanese or Korean, but it does not catch Latin spliced inside an Arabic word. The measured example is `"العملة الرسمية … هي الريyal السعودي"`, which passes every guard today and would reach a user as broken text.
- Decision: In phase 02, extend the script check in `app/text/script.py` and `validation_issues` with a **word-level rule**: any whitespace-delimited token that contains both Arabic and Latin letters fails validation. A small configurable allow-list covers tokens that are legitimately mixed; it lives in configuration, not in code (§20).
- Consequences: A whole-token rule stays deterministic and cheap, and it leaves ordinary mixed *sentences* alone — an Arabic answer may still quote a Latin term as its own word, e.g. `الرياض (Riyadh)`. The allow-list is the escape hatch for real mixed tokens, and it needs Arabic test cases like every other checker (CLAUDE.md rule 7). Not implemented in phase 01, so `"الريyal"` still reaches the user until phase 02.

### ADR-0034: An ADR's text is immutable; only its Status line may change
- Date: 2026-09-13
- Phase: 01 (fix session for PR #1)
- Status: decided
- Context: CLAUDE.md rule 10 and the header of this file say past entries are never rewritten. During phase 01 the owner nevertheless instructed that entries be updated in place as decisions were taken. Following that instruction, ADR-0011 to ADR-0023 were moved from "Assumed" to "Decided", ADR-0024 lost its original options and recommendation to the decision that closed it, and ADR-0032's proposal was replaced by the decision the owner took. The PR #1 review found that the log no longer shows what was proposed, when, or why it changed. The mistake was the owner's instruction, not the agent's reading of the rule; the owner says so here.
- Decision: An ADR's text is immutable once committed. Only its Status line may change, and only by appending a one-line note: `Accepted on <date>` or `Superseded by ADR-NNNN on <date>`. Any other change to a decision is a new entry that supersedes the old one. Entries stay in number order and never move between sections; the Status line carries the state. CLAUDE.md rule 10 is amended to say this. ADR-0011 to ADR-0024 are restored to their text at commit 79c6575; ADR-0031 and ADR-0032, which did not exist then, are restored to the text of the commit that first added them (18d78ea and 171487c). What was later written into ADR-0024 and ADR-0032 is re-recorded as ADR-0035 and ADR-0036.
- Consequences: The log reads as history: every decision keeps the proposal and evidence that produced it. A reviewer can check a `docs/DECISIONS.md` diff mechanically: lines may be added, and an existing entry may change only on its Status line. A decision that evolves several times accumulates entries instead of one polished text.

### ADR-0035: `general` runs on `standard`; `fast` is for instruction-following roles
- Date: 2026-09-13
- Phase: 01
- Status: decided
- Context: ADR-0024 put three options to the owner after the `fast` tier missed the `general` suite's 0.85 threshold, and recommended (a). The owner chose (b). The decision was first written into ADR-0024 itself; ADR-0034 restores that entry and records the decision here. The measurements behind it are ADR-0024's.
- Decision: **option (b)**. `services/agent/app/capabilities/general/manifest.yaml` sets `min_tier: standard`, and README §19.4 is patched to match. Owner's rationale: the `fast` tier exists for instruction-following roles (router, summarizer, judge, extractor), not for world knowledge. A general-knowledge answer depends on what the model knows, and a 3B model does not know enough in Arabic. Putting `general` on `fast` was an error in the spec, not a defect in the model.
- Consequences: General turns run on `qwen2.5:14b-instruct` and are slower; the added latency is not yet measured. Helper roles stay on `fast`, so the per-turn helper saving of §19 is untouched. The threshold stays at 0.85 and the suite grows instead (ADR-0028). Option (a) is not dead: it returns as ADR-0030, measured against helper-role work rather than general knowledge.

### ADR-0036: Decoding temperature is a per-role setting in `config/models.yaml`
- Date: 2026-09-13
- Phase: 01
- Status: decided
- Context: ADR-0032 proposed a temperature per role in `config/models.yaml` with `reasoning` at 0. `ModelGateway.call` sampled at a hard-coded `temperature=0.2`, chosen while building phase 01 and never recorded. With `answer.system@1.2.0` the `general` suite scored 18/20, 17/20, 18/20 (0.90, 0.85, 0.90) on three consecutive runs, one of them exactly on the threshold. The recurring failures are script drift by `qwen2.5:14b-instruct`, not missing knowledge. The owner accepted the proposal with a change: a value per role, with the answer keeping a little sampling. The decision was first written into ADR-0032 itself; ADR-0034 restores that entry and records the decision here.
- Decision: `config/models.yaml` gains a `temperature` block with one entry per role — `router`, `summarizer`, `judge` and `extractor` at **0.0**, `reasoning` and `reflection` at **0.2**. `ModelGateway` reads the temperature for `decision.role`; no temperature literal remains in code, and startup fails when a role has no temperature or the value is outside 0.0–2.0.
- Consequences: The helper calls that run on every turn — routing, summarizing, judging, extracting — become deterministic, so their output is comparable between runs and an eval regression there is attributable to a change rather than to sampling. The answer keeps a little sampling at 0.2, so **this does not remove the `general` suite's run-to-run variance**; it bounds non-determinism to the one call whose phrasing should vary. Adding a role to `roles` means adding its temperature too.

### ADR-0037: The answer prompt is capability-neutral
- Date: 2026-09-13
- Phase: 01 (fix session for PR #1)
- Status: decided
- Context: `answer.system@1.2.0` (ar) told the model to write the whole answer in Arabic letters and to use Latin letters only for a foreign name or term. The rule was tuned against the `general` suite, but `answer.system` is the core prompt for every capability's answer: SQL, column names, URLs and document identifiers are Latin and legitimately appear in Arabic answers from `sql`, `web`, `docs` and `kb`. The prompt changelog and a docstring in `app/context/tokens.py` also named served models, contradicting the rule that only `infra/litellm/config.yaml` names a model.
- Decision: `answer.system@1.3.0` (ar) keeps "write in Arabic script, no Chinese, Japanese or Korean characters" and drops the Latin ban and the word "entirely". Script drift is enforced deterministically by `validate` (ADR-0027) and, in phase 02, by the word-level mixed-script rule (ADR-0033), not by the prompt. The 1.2.0 changelog line and the `tokens.py` docstring no longer name a model.
- Consequences: Capabilities that quote Latin identifiers are not fighting the core prompt. The `general` suite is re-measured on 1.3.0 in this session; a Latin word spliced into an Arabic word (`الريyal`) is still caught only by the eval until ADR-0033 lands.

### ADR-0038: Capabilities run with a RuntimeContext
- Date: 2026-09-13
- Phase: 01 (fix session for PR #1)
- Status: decided — Superseded by ADR-0047 on 2026-09-13 (RuntimeContext surface, input package and output usage only)
- Context: `run_capabilities` invoked a capability subgraph with only `{"input": CapabilityInput}`. A capability could not reach `models.call`, the broker or the event stream without importing core services, its events were emitted only after it finished, and `_context_for` either assembled for the `reasoning` role without a step or, for a capability with no context needs (`general`), returned a package whose tier the resolver never chose and whose budget was 0. `ModelGateway.call` rejects a package assembled for a different tier, so the first capability with a step override would have failed. Rule 3 would then have forced a core change in phase 04.
- Decision: `app/capabilities/contract.py` defines `RuntimeContext` (slug, principal, `cap_state` slice, `models`, `broker`, `resolver`, `stream_writer`), passed to the subgraph as LangGraph runtime context (`Runtime[RuntimeContext]`). `models.call(role, step=..., request=...)` resolves the tier for (role, step) against the capability's manifest, emits a `tier` event, assembles context for that tier within `context.max_input_tokens`, calls LiteLLM and records usage. `broker.execute(name, args)` is the shared broker bound to the turn's principal and event stream. `stream_writer` is the main graph's writer, so events reach the client while the capability runs. The `CapabilityInput.context` package goes through the resolver at the capability's floor (no step) and gets that tier's real budget. An `AssemblyRequest` without a system prompt produces no system block, so a capability that does not need `system` ships no system prompt (ADR-0020). The gateway's tier check stays strict — the package tier must equal the decision's final tier — and step overrides pass because `models.call` assembles for the step's tier. `general` runs on the same path with no special case. `build_services` accepts extra capability roots; a test-only `probe` capability under `services/agent/tests/fixtures/capabilities/` calls a tool and a model on a step override, and an integration test blocks the capability until its `tool_end` event has reached the client.
- Consequences: Phase 04+ capabilities need no core change to call models, use tools or stream progress. Each capability call emits its own `tier` event, so the UI shows one tier chip per model call. `CapabilityOutput.events` still exists (§18.3) for events a capability returns rather than streams; they arrive after it ends. A capability's model tokens are not streamed as answer tokens. `smoke.sh` now reads block token counts from the answer node's `context.assemble` observation, since `general` produces one too.

### ADR-0039: Streamed text is retracted when a later check fails; phase 03 buffers output
- Date: 2026-09-13
- Phase: 01 (interim) and 03 (full fix)
- Status: decided — Superseded by ADR-0048 on 2026-09-13 (what the history stores only); Superseded by ADR-0079 on 2026-10-06 (the phase 03 full fix only)
- Context: The answer node streams tokens as the model produces them, but `validate`, `guard(OUTPUT)` and `guard(DATA)` run on the finished draft. A failing check ended the turn with an `error` event while the web client kept showing the text already streamed, so ADR-0027's "never reaches the user" did not hold. ADR-0031 proposed where `OUTPUT` and `DATA` should run relative to streaming; the owner accepted it with an interim for phase 01.
- Decision: Interim, from phase 01. A new SSE event `retract {reason}` withdraws every answer token streamed so far. When `validate`, a blocking `OUTPUT` policy or a blocking `DATA` policy fails after tokens were streamed, the failing node emits `retract` with the error code, then the turn ends with that `error` event (`validation_failed`, `output_blocked`, `data_blocked`). The web client empties the shown text on `retract` and renders the error in its place. The answer node records the streamed text as `shown_answer` (AgentState addition). `persist_state` appends the exchange with the text the user saw and, when a guard changed the draft without failing (a `DATA` redaction), keeps the post-guard draft on the same message as `post_guard_draft`. Full fix, phase 03, as ADR-0031 proposed: `DATA` runs on a sentence-level buffer during streaming and releases a sentence only once it is clean, so redaction happens before text leaves the agent; `OUTPUT` block policies run on the complete answer before streaming starts. The added time-to-first-token of both is measured and recorded with the phase 03 done-check. After that, `retract` remains only for checks no buffer can run early, such as `validate` on the complete answer.
- Consequences: Text that fails a check disappears from the UI, but it did reach the browser before the retract; phase 03's buffer removes that exposure. Until then a redacted answer streams unredacted, is redacted only in `done.answer` and `post_guard_draft`, and the history the next turn sees is the streamed text. `docs/events.schema.json` gains the `retract` type (additive; the schema id stays `events.v1`).

### ADR-0040: Model tier caps apply by the principal's roles
- Date: 2026-09-13
- Phase: 01 (fix session for PR #1)
- Status: decided — Superseded by ADR-0046 on 2026-09-13
- Context: `config/models.yaml` seeds `caps: {default: heavy, role:viewer: standard}` (README §19.1), but `TierResolver` read only `caps.default` and ignored the principal. The agent could not have applied a role cap anyway: the gateway forwards only `cap:*` and `kb:*:read` permissions (ADR-0006), and the realm had no `viewer` role (`viewer` was only a user name).
- Decision: The gateway forwards `roles` in the principal it sends to the agent: the realm roles that are not permissions, carry no `:` namespace, and are not Keycloak built-ins (`default-roles-*`, `offline_access`, `uma_authorization`); today `user`, `admin` and `viewer`. The agent's `Principal` gains `roles`. Roles never authorize anything; permissions still do. `TierResolver` takes the ceiling from the `caps` entries `role:<name>` that match the principal's roles, the tightest when several match, and `caps.default` when none match. The MODEL_TIER checkpoint sees that ceiling, and a capped decision's reason names the entry (`capped by caps.role:viewer`). The realm gains a `viewer` role, assigned to the seed user `viewer`.
- Consequences: `role:viewer: standard` takes effect. A user holding several capped roles gets the most restrictive ceiling; raising one role above the default needs a MODEL_TIER policy (phase 03), not a cap entry. README §11 still says the agent sees only `permissions[]`; it now also sees these roles, for tier ceilings only, and that sentence needs the owner's edit. A Keycloak volume keeps the realm imported on first boot, so an existing stack gets the `viewer` role only after `make clean && make up` or through the admin console. The agent request contract gains `principal.roles`, so the agent OpenAPI version is bumped with this session's contract drift fix.

### ADR-0041: A conversation belongs to the user and tenant of its first turn
- Date: 2026-09-13
- Phase: 01 (fix session for PR #1)
- Status: decided — Superseded by ADR-0050 on 2026-09-13 (where the agent checks ownership only)
- Context: The agent's LangGraph thread id is the client-supplied `conversation_id`, and neither the gateway nor the agent checked who owned it. `load_identity_and_state` replaced the identity fields but kept the thread's history, which Context Assembly puts into the model's context. With phase 02's durable Postgres checkpointer, any authenticated user who sent another user's conversation id would have read that history through the model (README §16).
- Decision: The gateway keeps `gateway.conversations (id uuid, user_id, tenant_id, created_at)` (Flyway V2). `POST /v1/chat/turns` binds a new id to the caller on first use (insert if absent, then read, so concurrent first turns agree on one owner) and refuses an id owned by another user or tenant with 403 `conversation_forbidden`, audited as `denied`, without calling the agent. The KB check runs first, so a refused KB selection never binds an id. Independently, the agent's `load_identity_and_state` refuses a thread whose stored `user_id` or `tenant_id` differs from the principal with the `error` event `conversation_forbidden`, before any history is read. Negative tests cover the gateway (another user, another tenant) and the agent (the same two cases, with the thread's history unchanged).
- Consequences: A conversation cannot be shared or handed over; that would be a feature of its own. The ownership table gains one row per conversation and is the natural base for listing conversations later. On the agent side, a refused run still writes the intruder's per-turn input fields (message, locale, selections) into the thread's checkpoint before the node refuses; history, summary and capability state are untouched, and the owner's next turn overwrites those fields. An existing stack gets the table when the gateway next starts (Flyway). The gateway contract documents a new 403 code, so the gateway OpenAPI version is bumped with this session's contract drift fix.

### ADR-0042: Audit is fail-closed
- Date: 2026-09-13
- Phase: 01 (fix session for PR #1)
- Status: decided
- Context: README §16 requires one audit record per turn. `ChatController` wrote the record after the stream ended and only logged a failed write (ADR-0013), so a turn could complete, answer included, with no record.
- Decision: Before anything else, the gateway checks that the audit log can take a record (`AuditRepository.check()`, a zero-row query on `gateway.audit_events`); if it cannot, the turn is answered with 503 `audit_unavailable` and the agent is never called. The single record per turn is still written when the outcome is known, but a streamed turn's record is written before its terminal event leaves: `SseRelay` forwards the agent's bytes unchanged one complete event block at a time, holds the `done` or `error` event (or the injected `agent_stream_interrupted` error) until the record is written, and when that write fails sends `retract` (ADR-0039) and an `audit_unavailable` error in its place. A refusal (`kb_forbidden`, `conversation_forbidden`, `agent_unavailable`) whose record cannot be written is answered with 503 `audit_unavailable` instead. A client that disconnects is still recorded as `aborted` on a best-effort basis, since nobody is left to refuse.
- Consequences: A database outage stops chat instead of silently losing audit records. If the log fails between the check and the final write, the answer's tokens have already reached the browser and are withdrawn by `retract`, the same residual exposure as ADR-0039. Each turn adds one short query. The gateway contract documents a new 503 code, so the gateway OpenAPI version is bumped with this session's contract drift fix. Supersedes only ADR-0013's handling of audit writes; the rest of ADR-0013 stands.

### ADR-0043: `make test` runs the gateway and web tests on every call
- Date: 2026-09-13
- Phase: 01 (fix session for PR #1)
- Status: decided
- Context: ADR-0019 runs the gateway and web tests in the `test` stage of their Dockerfiles. Docker caches that `RUN` layer, and the gateway's Gradle build cache (`org.gradle.caching=true`, kept in the cache mount) restores `:test FROM-CACHE`, so `make test` could report green without running either suite; PROGRESS had to re-run them by hand.
- Decision: `make test-gateway` and `make test-web` build their `test` target with `--no-cache-filter test`, so BuildKit always re-executes that stage while dependency and build stages stay cached. The gateway's test stage runs `gradle cleanTest test --no-build-cache`, so Gradle cannot restore results either. Supersedes only ADR-0019's test caching; the harness layout stands.
- Consequences: Every `make test` pays the full gateway and web test time (about a minute each on the development host). The result is proof that the suites ran against the current source.

### ADR-0044: Contract drift fails the tests; both API versions move to 1.1.0
- Date: 2026-09-13
- Phase: 01 (fix session for PR #1)
- Status: decided
- Context: README §6.3 and CLAUDE.md ("never change `docs/openapi/*.json` without bumping the API version") depend on the committed specs matching the code, but nothing compared them: `OpenApiExportTest` only wrote `build/openapi/gateway.json`. The agent spec also documented a 422 `HTTPValidationError` that the service never returns; invalid requests get the 400 error envelope.
- Decision: `services/agent/tests/unit/test_contracts.py` regenerates the agent OpenAPI document and the events JSON Schema and compares them with `docs/openapi/agent.json` and `docs/events.schema.json`; compose mounts `docs/` into `agent-tests` as `DOCS_DIR`. The gateway's `OpenApiExportTest` writes `build/openapi/gateway.json`, and a second, ordered test compares it with `docs/openapi/gateway.json`; the gateway Dockerfile's test stage receives that file through the named build context `docs` (`--build-context docs=docs`, passed by `make test-gateway`). The agent app removes FastAPI's automatic 422 response and its validation schemas from its spec. This session's contract changes — agent: `principal.roles`, 422 removed; gateway: 403 `conversation_forbidden`, 503 `audit_unavailable` — move both API versions from 1.0.0 to 1.1.0. The URL prefix stays `/v1` because every change is additive. `make openapi` regenerates the three files and the web's generated types; its gateway step runs only the export test.
- Consequences: A contract change without regenerated docs fails `make test`. The tests check equality, not the version bump itself; the version constant is named in the failure message and checked in review. Building the gateway's `test` target by hand now needs `--build-context docs=docs`; the runtime image does not.

### ADR-0045: Smoke counts streamed tokens; `make eval` records a Langfuse dataset run
- Date: 2026-09-13
- Phase: 01 (fix session for PR #1)
- Status: decided
- Context: `scripts/smoke.sh` judged streaming by a 0.05 s gap between the first token and the last event, which a buffered burst can pass. README §12 says `make eval` pushes a Langfuse dataset run; `evals/run_evals.py` only posted scores, and ignored Langfuse errors.
- Decision: Smoke requires at least 3 `token` events before `done`, replacing the time check. `run_evals.py` upserts one Langfuse dataset per suite (named after the suite), one dataset item per case (id `<suite>:<case id>`, input = message and locale, expected output = the case's `expect`), and creates one dataset run per invocation, `make-eval-<UTC timestamp>`, whose run items link each case to its turn's trace with pass/fail metadata. The `eval_pass` score stays. A failed Langfuse call now fails the eval run.
- Consequences: Runs can be compared side by side in Langfuse's dataset view. Item expectations follow the jsonl files on every run. A case that ends without a `done` event has no trace, so it is absent from the dataset run while still counted as a failure locally.

### ADR-0046: The gateway sends `principal.max_tier`; the agent never sees roles
- Date: 2026-09-13
- Phase: 01 (fix session for PR #1)
- Status: decided — Superseded by ADR-0049 on 2026-09-13 (ceiling arithmetic and the max_tier type only); Superseded by ADR-0107 on 2026-10-06 (the policy: role namespace only)
- Context: ADR-0040 made `role:viewer: standard` take effect by forwarding filtered realm roles to the agent. The owner rejected that approach: README §11 makes the gateway the only place that interprets Keycloak roles and gives the agent only `permissions[]`, and forwarding roles would weaken that boundary.
- Decision: The gateway reads `order` and the `caps.role:<name>` entries of `config/models.yaml` at startup (mounted read-only, path in `GATEWAY_MODELS_CONFIG`; an unknown tier fails startup) and turns the caller's realm roles into one `principal.max_tier`: the strictest matching ceiling, omitted when no role has one. The agent's `Principal` carries `max_tier` and no roles. `TierResolver` uses `principal.max_tier` as the ceiling, `caps.default` when it is absent, and refuses a tier it does not know. The `viewer` realm role from ADR-0040 stays. README §11 is unchanged.
- Consequences: Roles stay inside the gateway; the agent receives the decision, not its inputs. `config/models.yaml` stays the single place caps are written, now read by two services: the gateway reads the `role:` entries, the agent reads `default`, so changing a role cap needs a gateway restart. The gateway's test image receives `config/models.yaml` through the named build context `config`, which `make test-gateway` passes. The agent request contract changes from `principal.roles` to `principal.max_tier`; the agent OpenAPI version stays 1.1.0, because 1.1.0 has not left this unmerged PR (ADR-0044).

### ADR-0047: A capability sees a detached package and a narrow RuntimeContext
- Date: 2026-09-13
- Phase: 01 (second fix session for PR #1)
- Status: decided — Superseded by ADR-0052 on 2026-09-13 (the enforcement caveat in Consequences only)
- Context: The second review of PR #1 found that ADR-0038's contract leaked the core into capabilities. `CapabilityInput.context` kept its `AssemblyRequest`, whose `state` was the whole AgentState: every capability's `cap_state`, the raw `messages`, identity and permissions (README §18.4, §18.5). `models.call(request=...)` let a capability assemble any blocks it liked, bypassing `context.needs`. `RuntimeContext.resolver` exposed the tier resolver, its guard engine and the model config, and a resolve without a principal skipped the caller's ceiling. `stream_writer` accepted any event type, so a capability could stream a `done` that the gateway would take as the turn's terminal event and audit (ADR-0042). `CapabilityOutput.cap_state` defaulted to `{}`, silently wiping a namespace, and `CapabilityOutput.cost` was added to turn usage on top of the tokens `models.call` had already recorded.
- Decision:
  - **Detached input.** The capability's assembly request carries only the AgentState keys its `context.needs` read (`user_message` always; `messages` for `window`; `selected_kbs`, `decisions`, `open_threads`, `artifacts` for `state_digest`), never `cap_state`, identity or permissions. The package handed over is a copy of the assembled blocks without the request. `CapabilityInput.cap_state` and `RuntimeContext.cap_state` are separate deep copies of the capability's own namespace; only `CapabilityOutput.cap_state` is persisted. A later block that reads more state (summary, memory, retrieval) adds its keys to this projection.
  - **Narrow RuntimeContext.** Its fields are `slug`, `principal`, `cap_state`, `call_model`, `broker` and `stream_writer`. `call_model(role, *, step=None, extra_blocks=())` resolves the tier for (role, step) against the manifest with the turn's principal, emits the `tier` event, assembles through `context.needs` and records usage. `extra_blocks` must be `Block`s named `findings` and need `findings` in `context.needs`; they are rendered through `context.findings` like any evidence. No resolver, guard engine, model config or AgentState is reachable through the context's fields.
  - **Events.** `CapabilityEvent.type` is `tool_start | tool_end | citation | progress`; `progress {capability, step, current?, total?}` is a new additive event type in `docs/events.schema.json` (still `events.v1`). `stream_writer` takes one `CapabilityEvent`; anything else — including a `model_construct`ed event with a core type or an invalid payload — raises `CapabilityContractError`. Uncaught, the violation ends the turn as `turn_failed`; returned `events` are checked the same way.
  - **Output.** `CapabilityOutput.cap_state` is required, and `run_capabilities` revalidates the output as returned, so a missing `cap_state` ends the turn as `turn_failed`. `cost: Usage` stays (§18.3) but carries non-model cost only: a cost with tokens fails validation, and the node adds only `cost_usd`.
- Consequences: A capability can no longer read another capability's state, the raw history it did not declare, or the permissions list of the state, through anything the contract hands it; tests walk the input and context for sentinels. Python cannot stop deliberate reflection (`call_model`'s closure still references the turn's services), so the §18.4 lint rule recorded for phase 02 remains the enforcement for code review. The agent OpenAPI version stays 1.1.0, for the reason given in ADR-0046: 1.1.0 has not left this unmerged PR. The web ignores `progress` events until a UI for them is decided.

### ADR-0048: History holds the post-guard text; a reply whose audit fails is retracted in the agent too
- Date: 2026-09-13
- Phase: 01 (second fix session for PR #1)
- Status: decided — Superseded by ADR-0076 on 2026-10-06 (the user message of a retracted turn only)
- Context: ADR-0039 stored the text the user saw as the message `content`, with the post-guard draft beside it. After a DATA redaction the unredacted text was therefore what every later model call read, and what phase 02's durable checkpointer and memory writer would keep (README §10.1, §16). When the gateway's audit write for a finished turn failed (ADR-0042), the client was sent `retract`, but the agent had already persisted the reply, so the next turn's model saw an answer the user had been told was withdrawn. The web client also kept partial text on screen for every error except the three retract reasons: `model_unavailable` mid-stream, `turn_failed`, and the gateway's `agent_stream_interrupted`.
- Decision:
  - `persist_state` stores the post-guard `draft_answer` as the assistant message `content`. When it differs from the streamed text, the streamed text is kept as `shown_answer` in the message's `additional_kwargs`, for audit only; Context Assembly reads `content`. The assistant message also carries `turn_id`.
  - The agent gains `POST /v1/conversations/{conversation_id}/turns/{turn_id}/retract` with `{principal, reason}`, behind the internal token. It marks the assistant message of that turn `retracted` (with `retract_reason`) in place, idempotently; it answers 204, 403 `conversation_forbidden` for another owner, and 404 `turn_not_found`. Context Assembly skips retracted messages, in the window and in the history size measured by `assemble_context`. The user message of that turn stays.
  - The gateway's relay outcome carries the terminal event's `turn_id`. When the audit write of a turn that ended in `done` fails, the gateway calls the retract endpoint before sending `retract` and `audit_unavailable` to the client. Best effort: if the call fails, the failure is logged and the client is refused anyway. A turn that ended in `error` persisted nothing and is not retracted.
  - The web client clears streamed text on every `error` event, when a stream closes without a terminal event (`agent_stream_interrupted`), and on any other error it records, not only after `retract`.
  - This supersedes only ADR-0039's persistence line ("`persist_state` appends the exchange with the text the user saw"); the `retract` event and the phase 03 buffering plan stand.
- Consequences: Redacted data no longer re-enters model context through history, though it did reach the browser before phase 03's buffer, as ADR-0039 says. An audit-failed reply disappears from the agent's context when the retract call succeeds; if the agent is unreachable at that moment the reply stays in history and the log records it. The agent OpenAPI version stays 1.1.0, for the reason given in ADR-0046. A user who saw partial text followed by an error now sees only the error.

### ADR-0049: Caps are ceilings bounded by `caps.default`; every tier resolution names a principal
- Date: 2026-09-13
- Phase: 01 (second fix session for PR #1)
- Status: decided
- Context: Under ADR-0046 the gateway turned role caps into `principal.max_tier` without looking at `caps.default`, and the agent used `max_tier` *instead of* the default. A role cap looser than the default (`role:x: heavy` under `default: standard`) therefore raised the ceiling, although README §19.1 calls caps ceilings. `TierResolver.resolve` took an optional principal and fell back to `caps.default` without one, so any future call site that forgot it (router, summarizer, judge) would silently ignore the caller's ceiling. `principal.max_tier` was any string; an unknown value passed the API and failed inside the graph as `turn_failed`.
- Decision:
  - Effective ceiling = min(`caps.default`, role cap). The gateway's `TierCaps` requires `caps.default` and never returns a `max_tier` above it; it still omits `max_tier` when no role has a cap. The agent takes the lower of `caps.default` and `principal.max_tier`, and names `caps.default` in a capped decision's reason when the default is the binding ceiling.
  - `TierResolver.resolve(role, *, principal, ...)` requires the principal; there is no uncapped path.
  - The tier names are a `Literal` in `app/models/config.py` (`fast`, `standard`, `heavy`, the names of README §19.1). `principal.max_tier` is that enum in the agent API schema, so an unknown value is a 400 `invalid_request` before a turn starts. `config/models.yaml` must define exactly these tiers and a `caps.default`, or the agent fails at startup.
  - This supersedes ADR-0046's ceiling rule ("`principal.max_tier` as the ceiling, `caps.default` when it is absent") and its untyped `max_tier`; the rest of ADR-0046 stands.
- Consequences: A cap entry can only lower a ceiling; raising one role above the default remains a MODEL_TIER policy (phase 03). Adding a fourth tier is a code change to the `Literal` as well as to the config, which is deliberate: it changes the agent API. The agent OpenAPI version stays 1.1.0, for the reason given in ADR-0046.

### ADR-0050: The agent binds conversation ownership before a turn touches the thread; ids are lower-case UUIDs
- Date: 2026-09-13
- Phase: 01 (second fix session for PR #1)
- Status: decided
- Context: ADR-0041 checked ownership in the agent inside the graph's first node. By then LangGraph had already written the turn's input (message, locale, selections) into the owner's thread, and a thread whose first run was cancelled before that node saved had no recorded owner, so the next caller to reach the agent claimed it. The gateway's JDBC binding had no test against a real database. The gateway accepted upper-case UUIDs, bound them in their normalised form, but forwarded the raw string, so one conversation could become two agent threads; the agent accepted any string up to 64 characters.
- Decision:
  - `app/api/ownership.py` keeps a `ConversationOwners` registry. `TurnRunner` binds the conversation before the graph runs: the owner is the registry's binding, else the owner the checkpointed thread records, else the caller. The lookup and the insert happen with no await between them, so concurrent first turns agree on one owner. A caller who is not the owner gets the `error` event `conversation_forbidden` and the graph never runs, so nothing of the refused turn is written. The retract endpoint (ADR-0048) uses the same registry and never binds.
  - `load_identity_and_state` keeps its own check as defense in depth.
  - The gateway lower-cases `conversation_id` when it reads the request, so ownership, audit and the agent's thread id share one spelling. The agent's `TurnRequest.conversation_id` and the retract path require a lower-case UUID; anything else is a 400.
  - This supersedes ADR-0041's placement of the agent-side check ("the agent's `load_identity_and_state` refuses a thread…"); the gateway's binding and its 403 stand.
- Consequences: The registry lives in memory in phase 01, like the checkpointer; phase 02 must make it durable beside the Postgres checkpointer (recorded in PROGRESS). Until then an agent restart loses both, and the checkpointed owner still wins over a caller when only the registry is lost. A single agent process is assumed; several processes would need the durable registry first. The agent OpenAPI version stays 1.1.0, for the reason given in ADR-0046.

### ADR-0051: The gateway's JDBC tests run against the compose Postgres under the `test` profile
- Date: 2026-09-13
- Phase: 01 (second fix session for PR #1)
- Status: decided
- Context: The gateway tests run inside `docker build --target test` (ADR-0019, ADR-0043) with an in-memory conversation repository, and an image build cannot reach the compose network. The `INSERT … ON CONFLICT DO NOTHING` binding of ADR-0041 and the Flyway migrations therefore had no test.
- Decision: JDBC tests carry the JUnit tag `db`. The image's `test` task excludes the tag; a Gradle `dbTest` task includes only it and is never up to date. The gateway Dockerfile gains a `db-test` stage whose command runs `gradle dbTest`, and compose gains `gateway-db-tests` under the `test` profile, depending on the stack's `postgres` and reusing the `gateway-gradle-cache` volume that `make openapi` already uses. `make test` runs it as `make test-gateway-db`. `JdbcConversationRepositoryTest` connects as the `postgres` superuser, creates a throwaway database, applies the migrations with the same Flyway settings as `application.yml`, tests the first binding, a later caller, both spellings of one id and sixteen concurrent first binds, and drops the database. Without the connection settings the test fails, never skips.
- Consequences: `make test` now starts the stack's Postgres if it is not running and uses the Postgres superuser password from `.env` in a test container. The first run of `gateway-db-tests` on a machine downloads the Gradle dependencies into the shared cache volume. The migrations never touch the `app` database's data.

### ADR-0052: The core runs each capability subgraph in a fresh LangGraph config; lint bans the graph runtime APIs in capabilities
- Date: 2026-09-13
- Phase: 01 (third fix session for PR #1)
- Status: decided
- Context: The third review of PR #1 found that `run_capabilities` invoked a capability subgraph inside a main-graph node, so LangGraph handed the subgraph the main graph's `RunnableConfig` through a context variable. Inside any capability node, `langgraph.config.get_config()["configurable"]["__pregel_checkpointer"]` was the main graph's checkpointer, and `aget_tuple` on another conversation's `thread_id` returned that thread's whole AgentState: identity, `messages`, every capability's `cap_state`. The review reproduced it through `TurnRunner`: an attacker's capability, in its own conversation, read the victim's message. ADR-0047 left this kind of access to code review ("Python cannot stop deliberate reflection"), but `get_config` is a public LangGraph API, not reflection, and phase 02's Postgres checkpointer would have exposed every user's durable history the same way.
- Decision:
  - **Core.** `run_capabilities` invokes the subgraph in an asyncio task whose context clears LangChain's `var_child_runnable_config`, so the subgraph runs as a top-level graph with a fresh config: no checkpointer, no main-graph `configurable` (no `thread_id`, no checkpoint namespace), no inherited callbacks and no store. A capability reaches the core only through its `RuntimeContext` (ADR-0047). The rest of the context is kept, so OpenTelemetry spans still nest under `capability.{slug}`; the manifest timeout applies to the task. A capability graph compiled with its own checkpointer or store breaks the contract (`CapabilityContractError`, the turn ends as `turn_failed`).
  - **Lint.** `services/agent/app/capabilities/ruff.toml` extends the agent's ruff settings with ruff's banned-api rule (TID251) for `langgraph.config.get_config`, `langgraph.config.get_stream_writer`, `langgraph.config.get_store` and the whole `langgraph.checkpoint` package (every checkpointer class, the Postgres saver included). `tests/unit/test_capability_lint.py` proves each ban fires under `app/capabilities/` and not in core code, and lints `app/capabilities/` itself, so `make test` fails on a violation without a separate make target.
  - **Tests.** An integration test runs a victim's turn, then an attacker's turn in another conversation whose capability calls `get_config()` and `aget_tuple` on both thread ids through anything the config holds; it must obtain no state. It runs on the main graph's own checkpointer and is the regression test for phase 02's Postgres checkpointer. A unit test runs `run_capabilities` inside a main graph compiled with a checkpointer and a store and invoked with a thread id and a callback handler, and checks that none of them reaches the subgraph.
  - LangGraph puts `__pregel_checkpointer` into the `configurable` of every top-level run, holding `None` when the graph has no checkpointer. The tests therefore assert that the key holds no saver, not that it is absent; removing the key would mean patching LangGraph.
  - This supersedes ADR-0047's enforcement caveat ("Python cannot stop deliberate reflection (`call_model`'s closure still references the turn's services), so the §18.4 lint rule recorded for phase 02 remains the enforcement for code review") for LangGraph's config, checkpointer, store and stream writer: that isolation is enforced by the core and by lint, not by capability discipline. The rest of ADR-0047 stands.
- Consequences: A capability subgraph cannot use LangGraph persistence of its own: no checkpoints, no store, and no `interrupt()` that reaches the main graph. Phase 08's HITL interrupt must be raised by the core in the main graph's context (for example, the broker returns `pending_approval` and `run_capabilities` interrupts), not from inside a capability subgraph. Not closed by this entry: the callables in `RuntimeContext` are core objects, so plain attribute access reaches the turn's services (`runtime.context.broker._ctx.services.graph`, or `call_model.__closure__`); neither the fresh config nor this lint rule stops that. The cross-namespace `cap_state` lint rule of README §18.4 stays phase 02 tech debt. No API or event contract changes.

### ADR-0053: Capability isolation is one exception/return firewall in the core, plus type-checked compilation, plus lint
- Date: 2026-09-13
- Phase: 01 (fourth fix session for PR #1)
- Status: decided — Superseded by ADR-0054 on 2026-09-13 (the open attribute-reflection item only)
- Context: The fourth review of PR #1 found that ADR-0052's fresh config stopped a capability from *reading* the parent checkpointer, config or store, but not from *escaping* into the main graph through LangGraph's own control flow. `run_capabilities` awaited the subgraph but caught only `TimeoutError` and `CapabilityContractError`, so anything else the subgraph raised propagated into the main-graph node and reached LangGraph's retry handler. A capability node that raised `ParentCommand(Command(graph="run_capabilities", update={...}))` — a public `langgraph.errors`/`langgraph.types` API, and the shape a returned `Command(graph=PARENT)` also takes — had its update applied to the main graph's state and checkpointed: the review forged `user_id`, `permissions`, another capability's `cap_state` namespace and a `messages` entry, and the turn still ended `done`. A raised `GraphInterrupt` left the thread with a pending interrupt, which phase 08's resume path would act on. Separately, `registry.py` accepted whatever `build()` returned (`graph: Any`), so a capability could hand the core any object with an `ainvoke`, including one whose `ainvoke` raised a forged `GraphInterrupt`. The enumerated bans of ADR-0052 do not stop any of this: they name specific reader APIs, not the open set of ways to raise a control-flow signal.
- Decision:
  - **Firewall (core).** `_invoke_isolated` runs the subgraph in the isolated task (ADR-0052) and is now the sole boundary. Any `BaseException` the task raises other than `asyncio.CancelledError` — `ParentCommand`, `GraphInterrupt`/`GraphBubbleUp`, a returned `Command` (which LangGraph raises as `ParentCommand`), a timeout, or any other exception — becomes `CapabilityFailed`; a subgraph that returns something other than a valid `CapabilityOutput` becomes `CapabilityFailed` too. `run_capabilities` records the failure on the `capability.{slug}` span (the trace channel that already carries tool and guard events, §12) and ends the turn with the new error code `capability_failed` (retryable for a timeout). Nothing a capability raises or returns reaches the main graph's retry handler or its checkpoint. `CancelledError` (a cancelled turn, e.g. a client disconnect) is the one signal that must not be swallowed: the subgraph task is cancelled and drained, then it is re-raised so the runner ends the turn as cancelled.
  - **Type-checked compilation (core).** `build()` returns an uncompiled `StateGraph`; the core's registry compiles it (`_compile_capability`) with no checkpointer, store or `interrupt_before/after`, so a capability cannot arrange its own persistence or interrupts. Any other return type is a `ManifestError` at startup. This changes README §18.1/§18.3, which described `build()` as returning a compiled subgraph: the core now owns compilation. The `Capability` protocol's intent is unchanged; only who calls `.compile()` moves.
  - **Lint (secondary).** `app/capabilities/ruff.toml` additionally bans, under `app/capabilities/`, `langgraph.errors`, `langgraph.types.Command`, `langgraph.types.interrupt`, and the `langgraph.pregel` and `langgraph.checkpoint` packages. These bans are a lint-time signal that a capability is reaching for machinery the firewall already neutralises at runtime; they are not the enforcement.
  - The order of defence is deliberate: the runtime firewall is the guarantee, type-checked compilation removes the load-time hole (an arbitrary object, own interrupts), and the enumerated bans are a secondary tripwire. This inverts ADR-0052's framing, where the bans carried more weight.
  - `capability_failed` joins `config/messages/errors.yaml`, the web i18n catalogs and `KNOWN_ERROR_CODES`. It is a free-string error `code`, so no OpenAPI or events-schema change; the agent OpenAPI version stays 1.1.0 (ADR-0046).
- Consequences: A capability can fail the turn but can no longer alter it: the worst a broken or hostile capability subgraph does is end its own turn as `capability_failed`, recorded on the trace. Because an errored turn persists nothing (ADR-0048), the failure record lives on the span, not in the checkpoint's `tool_calls`. Still open, unchanged from ADR-0052: `RuntimeContext`'s callables are core objects, so plain attribute access still reaches the turn's services (`broker._ctx.services`, `call_model.__closure__`); the firewall bounds *control flow*, not attribute reflection. True isolation against that — and the only real defence if capabilities ever become third-party rather than first-party code in this repo — is process-level isolation: running each capability subgraph in a separate worker with only its `RuntimeContext` marshalled across the boundary. That is recorded as the phase-09 hardening option, not built now, because v1's five capabilities are first-party. The cross-namespace `cap_state` lint rule of README §18.4 stays phase 02 tech debt.

### ADR-0054: The capability boundary is data-only; capabilities are trusted first-party code, and process isolation is required before any third-party capability
- Date: 2026-09-13
- Phase: 01 (fifth fix session for PR #1)
- Status: decided
- Context: The fifth review of PR #1 found that ADR-0053's firewall caught what the capability task raised, but core code in `run_capabilities` then touched objects the capability had created, outside the task, back in the main graph's node. The firewall's handler formatted the caught exception (`f"...: {exc}"`), which ran the exception's `__str__`; `_output` ran `isinstance(raw, BaseModel)` and `vars(raw)` on the returned output, which ran a `__class__` property. Either method could raise `ParentCommand(Command(graph="run_capabilities", update={...}))` there, where nothing caught it: the review forged `user_id`, `tenant_id`, `permissions`, another capability's `cap_state` namespace and a `messages` entry into the checkpoint, and the `__str__` variant still ended the turn `done`. A control-flow firewall is only as strong as the rule that nothing capability-made crosses it.
- Decision:
  - **Data-only boundary (core).** Inside the isolated task and behind the same catch-all, `_run_to_json` runs the subgraph, reads its `output` and serializes it with `model_dump(mode="json")` (`to_jsonable_python` for a dict or any other value) to JSON text. The task returns only core-built data, `(True, json_text)` or `(False, reason)`. `run_capabilities` receives that text and re-validates it into a `CapabilityOutput` with `model_validate_json`; an output that does not validate, a `model_construct` instance lacking `cap_state` included, is `capability_failed`, and so is an output with no JSON form. No object created by capability code crosses into `run_capabilities`.
  - **Exception reporting by type name only.** Inside the firewall a failure is reported as `raised <type(exc).__name__>`, never `str(exc)` or `repr`; a type name that raises or is not an exact `str` becomes a fixed name. A cancellation is re-raised as a fresh `CancelledError` outside the handler, so not even the exception object crosses. The `isinstance`/`vars` path on capability-returned objects is removed.
  - **Threat model.** v1 capabilities are trusted first-party code in this repository, reviewed like the core. The firewall (ADR-0053), this data-only boundary, compilation by the core (ADR-0053) and the capability lint (ADR-0052, ADR-0053) protect against bugs and accidental coupling: a capability that raises, returns the wrong thing or reaches for LangGraph machinery ends its own turn as `capability_failed` and cannot alter the main graph's state, checkpoint, interrupts or another thread through them. They do not protect against a deliberately malicious capability. In-process Python cannot: attribute reflection (`runtime.context.broker._ctx.services`, `call_model.__closure__`), stack frames (`asyncio.all_tasks()`, `cr_frame`), `gc` and monkeypatching all reach core objects, and no amount of in-process checking closes them.
  - **Third-party capabilities.** Process-level isolation — each capability subgraph in a separate worker process, with its input, `RuntimeContext` calls and output marshalled as JSON over IPC — is the phase-09 hardening option and is required before any third-party capability is loaded. This boundary is shaped for that move: only JSON already crosses it.
  - ADR-0053's open item (`RuntimeContext`'s callables are core objects, so attribute reflection reaches the turn's services; process isolation recorded as the option for third-party code) is covered by this threat model: accepted for first-party code, not a defect to fix in-process. The rest of ADR-0053 stands.
- Consequences: An output costs one JSON serialization and one validation per capability call; v1 outputs are small. A capability output must be JSON-serializable, which `CapabilityOutput`'s fields already implied (it is streamed and checkpointed). A failure's span and log record carry the exception type only, so debugging a capability exception means reproducing it in the capability's own tests rather than reading the message from the trace. Code review remains the control against a capability that means harm, until the phase-09 worker exists. No API, event or error-code change.

### ADR-0055: State, conversation owners and memory live in schema `agent` of database `app`
- Date: 2026-09-15
- Phase: 02
- Status: assumed — Superseded by ADR-0069 on 2026-10-05 (the migration record only); Superseded by ADR-0070 on 2026-10-05 (how tests reach Postgres only); Accepted on 2026-10-06
- Context: README §2 puts state in the LangGraph Postgres checkpointer, ADR-0016 kept `InMemorySaver` until phase 02, and ADR-0050 recorded that the conversation-owner registry must become durable beside the checkpointer. The spec names the database (`app`) but not the schema, the connection handling, the agent's own tables or how tests reach Postgres.
- Decision:
  - The agent connects to database `app` as its owner role with one `psycopg` async pool whose connections set `search_path=agent,public` (`public` holds the `vector` extension). `AsyncPostgresSaver` runs on that pool and creates its tables in schema `agent`; the gateway keeps schema `gateway`.
  - The agent's own tables (`conversation_owners`, `memory_items`, `memory_settings`, `context_packages`) come from SQL files in `app/db/migrations/`, applied once each, in name order, under a Postgres advisory lock, recorded in `agent.schema_migrations`. The checkpointer applies its own migrations beside them.
  - `ConversationOwners` keeps its interface (ADR-0050) on the table: the binding is an `INSERT … ON CONFLICT DO NOTHING` followed by a read, so concurrent first turns agree on one owner; an owner the checkpoint records still wins when the row is missing.
  - `AsyncPostgresSaver` binds to the running event loop, so `build_services` stays synchronous and connects nothing; `start_services` (the app lifespan, or a test fixture) opens the pool, runs both migrations and compiles the main graph. `DATABASE_URL` is set by compose from `APP_DB_PASSWORD`.
  - Tests use a throwaway database per session, created and dropped as the Postgres superuser like the gateway's JDBC tests (ADR-0051); `make test` passes `AGENT_TEST_DATABASE_ADMIN_URL` and starts Postgres. `test_capability_isolation.py` runs unchanged on the Postgres checkpointer. A restart test builds a second set of services, pool and checkpointer on the same database.
- Consequences: A conversation, its owner binding and the user's memory survive `docker compose restart agent` and `make down && make up`; `make clean` deletes them. The agent now starts only after Postgres is healthy. The `app` role owns every table in both schemas, as ADR-0013 already noted for audit. `make test` needs Postgres as well as the mock LiteLLM.

### ADR-0056: Context Assembly is configured per block, and every model call takes one path
- Date: 2026-09-15
- Phase: 02
- Status: assumed — Accepted on 2026-10-06
- Context: README §7 lists nine steps, a drop order and a `NeedsMore` that the tier resolver resolves "per config" by compressing or escalating, then assembling "once more". It gives no per-block budgets, no definition of the harder pass, and no place where the resolution happens. Phase 01 raised `context_overflow` on the first `NeedsMore` and each call site repeated resolve → budget → assemble → call.
- Decision:
  - `config/context.yaml` names the pipeline (system, state digest, rolling summary, sliding window, memory, semantic retrieval, tool results, findings, user message, budget guard), a token budget per block, the `drop_order`, the `compress` pass (a shorter window, smaller memory and retrieval `top_k`, shorter summary and tool results), the summary trigger and the core nodes (`answer`, `summarizer`, `memory_writer`) with their prompts, evidence forms (`full | summary | reference`) and per-node block budgets. Each step keeps its own block within its budget and records every cut as a `Drop`; the budget guard enforces the tier budget in `drop_order`: window messages oldest first, chunks lowest score first, the memory block, then the summary shortened from its oldest end. System instructions and the user message are never dropped. Over-budget chunks, tool results and findings are compressed extractively (sentences sharing the most words with the question, after Arabic normalization).
  - `app/graph/calls.py` is the one path for every model call: `prepare_call` resolves the tier, assembles for that tier's budget and resolves a `NeedsMore` once — one tier up (`TierResolver.escalate`, bounded by the same cap and by `max_escalations_per_turn`, recorded in `escalations`) when the overflow action is `escalate` and a higher tier is allowed, otherwise the compress pass — and ends the turn as `context_overflow` if the package still does not fit. `call_model` sends it through the model gateway and records usage once. The answer node, a capability's input package and `call_model`, the summarizer and the memory extractor all use it. The overflow action is the manifest's `on_overflow` (`escalate` wins among several capabilities), else `escalation.context_overflow_default`. This is step 3 of README §19.3; router and validation escalation stay phase 08.
  - Only the calls a user sees produce a `tier` event (the answer and capability calls). The summarizer and the extractor are helper calls: their tier decision is on the span, not in the stream.
  - Semantic retrieval and tool-result steps exist now and render what a request carries; no capability produces chunks or tool results until phases 04–07.
- Consequences: Adding a block budget or changing the drop order is a config change. A capability with `on_overflow: escalate` can move a call one tier up; `general` compresses. A package that fits neither pass ends the turn before any model call, so nothing is persisted. The smoke check that every tier event of the viewer's turn is `standard` still holds, because helper calls stream no tier event.

### ADR-0057: The rolling summary folds what leaves the window once the history passes the trigger
- Date: 2026-09-15
- Phase: 02
- Status: assumed — Superseded by ADR-0073 on 2026-10-05 (where the fold runs only); Accepted on 2026-10-06
- Context: README §7 step 4 summarizes messages older than the window incrementally, with the trigger "history > 8k tokens". Taken literally, a history that fell back under the trigger after a first summary would leave messages that are neither in the summary nor in the window.
- Decision:
  - History is the summary plus the unsummarized, unretracted messages. When it exceeds `summary.trigger_tokens` (8000), every message older than the window and not yet summarized is folded; once a summary exists, every later turn folds the messages that left the window. `summary_cursor` (AgentState addition) counts the messages the summary covers, and the sliding window reads only messages after it.
  - The fold runs in `assemble_context`, before routing, through `prepare_call` with the `summarizer` role (tier and temperature from `config/models.yaml`). The request holds the old summary in the `summary` block, the messages being folded in the `window` block (their own budget in `nodes.summarizer.blocks`), and the versioned `summarizer.request` prompt as the user block. Retracted messages are never folded (ADR-0048). `summary` and `summary_cursor` are checkpointed.
  - An empty summary keeps the cursor, so the same messages are folded next turn; a model failure ends the turn like any other call.
- Consequences: A long conversation costs one `fast` call per turn once it passes the trigger, and none before. A reply retracted after it was already folded stays in the summary; retraction happens right after a turn, while the reply is still in the window. Summary quality on `fast` is measured with the Arabic samples recorded in PROGRESS (material for ADR-0030).

### ADR-0058: Memory write, retrieval and policy
- Date: 2026-09-15
- Phase: 02
- Status: assumed — Superseded by ADR-0071 on 2026-10-05 (memory extracted from a reply that is later retracted only); Accepted on 2026-10-06
- Context: README §8 fixes the table, the kinds, the similarity dedupe and the rule never to store what a guard flagged, and describes an asynchronous writer. The owner scoped phase 02 to a synchronous graph node after `persist_state` that reads post-guard content and calls the `extractor` role. Defaults, failure handling and the embedding path were open.
- Decision:
  - `agent.memory_items` carries README §8's columns plus `id`; `embedding` is an untyped `vector`, and similarity (cosine) is computed only against vectors of the query's dimension, so the embedding model can change without a migration. Every statement names `(tenant_id, user_id)`; no store method crosses it. `agent.memory_settings` holds the per-user switch; without a row, `enabled_by_default` (true) applies.
  - Embeddings go through LiteLLM's `embed` alias (`config/models.yaml`), one text per call. An embedding is not a chat call, so it has no ContextPackage.
  - `write_memory` runs after `persist_state` and reads the two messages that turn persisted, role and `content` only — never `shown_answer`. It writes nothing when the user's memory is off, or when any guard event of the turn has an action other than `allow` or `log_only`. The extractor (`memory.extractor` prompt) answers with a JSON array; candidates of the configured kinds, at or above `min_confidence`, up to `max_items_per_turn`, are embedded and stored unless a live item of the same scope is at least `dedupe_similarity` similar. A malformed answer stores nothing. Its latency is `memory.write_ms` on the turn span. Any failure is recorded on the span and never fails the turn, since the reply is already persisted.
  - Retrieval is the `memory` block for the request's `memory_scope`, set by core code from the principal: the top `blocks.memory.top_k` items by similarity to the user message, at least `min_similarity`. A user with memory off gets nothing; an embedding failure degrades to no memory for that call, recorded on the span. The prompt marks memory as data, not instructions.
  - All values live in `config/memory.yaml`. The mock LiteLLM returns one fixed embedding and a valid extraction on `fast`, so tests exercise the write, dedupe and retrieval paths.
- Consequences: A general turn costs one more `fast` call and two embeddings when memory is on. Retrieval uses the message alone, not the intent, which the router does not produce yet. PII categories are excluded only by the extractor prompt until phase 03 puts memory text through guards; memory extracted from a reply that is later retracted stays until its user deletes it; the write moves to an asynchronous worker in phase 09. There is no ANN index; per-user scans are small in v1.

### ADR-0059: Endpoints without a body name the caller in headers; memory is proxied and audited; the debug endpoint checks ownership itself
- Date: 2026-09-15
- Phase: 02
- Status: assumed — Accepted on 2026-10-06
- Context: The memory list/delete/switch and `GET /v1/debug/context/{turn_id}` have no request body in which to carry a principal, unlike `POST /v1/turns`. README §8 says users manage their own memory and admins cannot read it; README §7 calls the debug endpoint admin-only; residual risk 7 requires it to check conversation ownership itself. Debugging is environment-specific, which §20 keeps out of `config/`.
- Decision:
  - Agent endpoints without a body take the caller from `X-User-Id` and `X-Tenant-Id`, beside `X-Internal-Token`. Only the gateway sets them, from the verified token; a client's own headers of that name are never forwarded.
  - Agent: `GET /v1/memory` (switch and items), `DELETE /v1/memory/{id}` (204, or 404 `memory_not_found` for an id the caller does not have, another user's included), `PUT /v1/memory/settings`. Gateway: the same three paths, answered in the envelope and audited like turns (ADR-0042): refused with 503 `audit_unavailable` before the agent is called when the log is unreachable, and answered only once the record (`memory.list`, `memory.delete`, `memory.settings`) is written. A delete of an item the caller does not have is audited as `denied`; a malformed id is rejected by validation and not audited (ADR-0013). There is no endpoint that reads another user's memory, for any role.
  - `GET /v1/debug/context/{turn_id}` is served, and packages are recorded in `agent.context_packages` by `prepare_call`, only while `DEBUG_CONTEXT_ENABLED` is true (`.env`, default false); otherwise it answers 404 `not_found`. It resolves the turn's conversation, reads that conversation's owner (`TurnRunner.owner_of`) and answers 403 `conversation_forbidden` to anyone else, before returning the exact packages in assembly order. Phase 02 does not proxy it through the gateway; the admin panel of README §13 decides who may call it there.
- Consequences: An admin reads context packages only for conversations they own until the gateway proxy defines admin access, which is stricter than README §7. Recorded packages hold user content and have no retention yet, so the flag stays off outside development. A delete that succeeds in the agent but whose audit record then fails is answered 503 although the item is gone, the same residual as a turn (ADR-0042). CORS allows `PUT` and `DELETE`.

### ADR-0060: Writing-script rule for Arabic answers; supersedes ADR-0033 and ADR-0027's CJK check
- Date: 2026-09-15
- Phase: 02
- Status: decided
- Context: ADR-0033 decided a word-level Arabic–Latin rule for phase 02. The owner widened it when scoping the phase: ADR-0027 caught only CJK, and the measured drifts include `الريyal`, `البرتugal` and `لисboa`, where Cyrillic also appears.
- Decision:
  - For an Arabic-locale answer the allowed scripts are Arabic, Latin, digits and punctuation. Latin is allowed only as whole tokens: any whitespace-delimited token that mixes letters of two scripts fails (`mixed_script_token`), and a letter of any other script (Cyrillic, Greek, CJK, …) fails anywhere (`disallowed_script`). These codes replace `cjk_in_arabic_answer`.
  - `config/validation.yaml` holds, per locale, the allowed letter scripts and `mixed_token_allowlist` (tokens compared after trimming punctuation at their ends). A locale without an entry is not script-checked, as English was not under ADR-0027.
  - A character's script is the first word of its Unicode name for letters and marks; digits, punctuation, symbols, separators, format characters and combining or modifier marks carry none. Deterministic and model-free, in `app/text/script.py`; tests cover the three measured samples and a pure-Cyrillic token.
- Consequences: `الرياض (Riyadh)`, URLs, SQL and Arabic-Indic digits pass; `الـAPI` fails unless allowlisted. The answer node retries a failure once before `validate` ends the turn (ADR-0061).

### ADR-0061: A script failure is retried once on the same package; the retry replaces the streamed text
- Date: 2026-09-15
- Phase: 02
- Status: assumed — Accepted on 2026-10-06
- Context: The owner asked the answer node to retry a script-validation failure once at temperature 0.0 through the same Context Assembly → models.call path, record `answer.retry` on the span, and retract only if the retry also fails. Tokens of the first attempt have already streamed by the time its text can be checked, so the client needs a way to replace them, and ADR-0036 keeps temperatures out of code.
- Decision:
  - After the answer streams, the answer node applies the locale's script policy. When an issue listed in `retry.issues` is found and `retry.attempts` is 1, it emits `retract` with reason `answer_retry`, calls the model again on the same prepared package and tier at `retry.temperature` (0.0, `config/validation.yaml`, passed to the model gateway in place of the role's temperature), and streams the retry in place of the first text. The span records `answer.retry`, `answer.retry.issues` and `answer.retry.passed`. `validate` then checks the retry; if it fails too, it retracts with `validation_failed` and ends the turn as before. `attempts: 0` turns the retry off.
  - The web client already clears streamed text on `retract` and appends later tokens, so no client or event-schema change is needed; the turn's `error` still follows only a failed retry.
- Consequences: A retracted first attempt does not end the turn: the `retract` event now also marks a replacement. A retried turn costs a second answer call on the same tier; escalating a tier on validation failure stays phase 08. Scores with and without the retry are recorded in PROGRESS.

### ADR-0062: The cross-namespace `cap_state` rule is an AST check in `make test`
- Date: 2026-09-15
- Phase: 02
- Status: assumed — Accepted on 2026-10-06
- Context: README §18.4 enforces "each capability reads/writes only `state[state_namespace]`" by a runtime guard and a lint rule. The runtime slice exists (ADR-0047); ruff has no rule for string keys.
- Decision: `tests/unit/test_cap_state_lint.py` parses every Python file of every capability package (`app/capabilities/*`, and the test-only capabilities under `tests/fixtures/capabilities/*`) and fails on a string constant that is another capability's namespace (`cap.<slug>` other than the manifest's own `state_namespace`) or the AgentState key `cap_state`. A capability uses `runtime.context.cap_state` and returns `CapabilityOutput(cap_state=...)`, neither of which is a string key. Tests prove the rule fires on the forbidden shapes and passes on the allowed ones.
- Consequences: Building a namespace name dynamically escapes the rule, like any lint; the runtime slice (ADR-0047) still hands a capability its own namespace only, and first-party code review remains the control (ADR-0054).

### ADR-0063: Agent and gateway API versions move to 1.2.0
- Date: 2026-09-15
- Phase: 02
- Status: decided
- Context: ADR-0046 kept 1.1.0 while its changes had not left the unmerged PR #1. PR #1 is merged, so 1.1.0 is published and phase 02's additions need a new version.
- Decision: Agent OpenAPI 1.2.0 adds `GET /v1/memory`, `DELETE /v1/memory/{item_id}`, `PUT /v1/memory/settings` and `GET /v1/debug/context/{turn_id}`; gateway OpenAPI 1.2.0 adds the three memory endpoints and the `memory_not_found` code. Every change is additive, so the URL prefix stays `/v1`. `docs/openapi/*` and the web's generated types are regenerated; `docs/events.schema.json` is unchanged (`events.v1`).
- Consequences: The drift tests (ADR-0044) pin both documents at 1.2.0.

### ADR-0064: A memory candidate must name the user, be grounded in the user's message and keep to the locale's scripts
- Date: 2026-09-15
- Phase: 02
- Status: assumed — Accepted on 2026-10-06
- Context: ADR-0058 keeps candidates by kind, confidence and similarity. Probed on the live stack with four exchanges (PROGRESS), the `fast` extractor (temperature 0) answered `[]` even for an explicit role and preference until the prompt carried worked examples. With examples it extracted the smoke message, but it also produced an inferred fact for a pure general-knowledge question ("المستخدم يرغب في الحصول على معلومة تاريخية حول عاصمة بلد معين"), later the answer itself as a fact ("عاصمة البرتغال هي لشبونة."), turned a formatting preference into "المستخدم معرف بجامعة الملك سعود", and drifted into mixed script ("ي.work على مشروع…"). Every stored item is retrieved into later answers. README §2 keeps the policy over what is worth remembering in our code.
- Decision: `parse_candidates` keeps a candidate only when, in addition to ADR-0058's rules:
  - its text names its subject, one of `subject_words` (`المستخدم`, `user`): memory is about the user, not about the topic asked;
  - its text passes the conversation locale's script policy (`config/validation.yaml`, ADR-0060);
  - at least `min_grounding` (0.5) of its content words appear in the user's own message of that exchange. Content words have at least three letters, are compared after Arabic normalization with light proclitics (`و`, `ب`, `ل`, `ف`, `ك`, `ال` and their combinations) stripped, and exclude `grounding_ignore`, the words the extractor's phrasing adds to every candidate. The light stemmer lives in `app/text/arabic.py` beside normalization, for the phase 03 keyword checker too.
  The extractor prompt carries three worked examples (facts, a preference only, a question only), asks that every text start with "المستخدم" / "The user", and that a question alone yield `[]`.
- Consequences: Inferences, restated answers and drifted text are not stored; a faithful paraphrase whose verbs change person (`أعمل` → `يعمل`) still passes on its nouns. A candidate about the user's project that does not name the user is dropped, a false negative accepted over a false memory. A misattribution built from the user's own words can still pass; that is extractor capacity, recorded as material for ADR-0030. Tests whose mock extraction must be stored send a message containing its words.

### ADR-0065: Dev tiers on OpenRouter with open-weight models that mirror production
- Date: 2026-09-16
- Phase: 02
- Status: assumed — Decided by the owner on 2026-10-05. Note: `evals/run_evals.py` logs in before each case (owner-approved), because a run outlasts the realm's 300 s access token. Superseded by ADR-0066 on 2026-10-05 (the two-file offline profile only); Superseded by ADR-0067 on 2026-10-05 (the tier windows and `max_output` only)
- Context: On the development host the tiers ran on local Ollama (ADR-0008): `qwen2.5:3b`, `qwen2.5:14b-instruct` and `gemma4:31b` at 32k/32k/65k context. Phase 02 measured defects that come from those models, not from the platform: the `fast` summarizer wrote in the assistant's voice and turned open choices into decisions, the `fast` extractor inferred and misattributed facts (ADR-0064), and the `standard` model drifted into mixed script at temperature 0 (0 of 11 retries rescued an answer, ADR-0061). ADR-0030 proposed measuring a 7B `fast` model on the same host. The owner moved the three chat tiers to OpenRouter instead, with open-weight models of the kind production can host; `infra/litellm/config.yaml` and the `OPENROUTER_API_KEY` in `docker-compose.yml` are the owner's edits.
- Decision:
  - `fast`, `standard` and `heavy` resolve to open-weight models on OpenRouter, each with `provider.allow_fallbacks: false`; `embed` stays on local Ollama (`bge-m3`), so no stored vector changes. `infra/litellm/config.yaml` stays the only file that names a model. `OPENROUTER_API_KEY` is in `.env.example`, empty.
  - No real or ministry data in development while the tiers are on OpenRouter: seed users, synthetic conversations and public facts only. Prompts and completions leave the host.
  - `config/models.yaml` windows and `max_output` are the smallest context length and completion limit among each model's OpenRouter providers (read from `/api/v1/models/<model>/endpoints` on 2026-09-16), because no provider is pinned: `fast` 131072/8192, `standard` 256000/65536, `heavy` 1000000/32768.
  - The Ollama aliases are kept as the offline profile, `infra/litellm/config.local.yaml`, selected by the overlay `docker-compose.local.yml`: `COMPOSE_FILE=docker-compose.yml:docker-compose.local.yml make up smoke`, then `make up` to switch back. The owner runs `make smoke` on it once per phase before merge (HANDOFF owner checklist), so the stack keeps working without a network.
  - `config/validation.yaml` sets `retry.attempts: 0`: the retry never rescued an answer on the old `standard` model. The code path and its tests stay; the tests set `attempts` themselves.
  - ADR-0030 is superseded: the `fast` model is no longer chosen by what the development host can hold. The helper-role suite it asked for is still the right measurement for any `fast` model.
- Consequences: Development needs network access and OpenRouter credit; OpenRouter holds a call's full `max_tokens` against the balance, so a 65536 ceiling on `standard` fails with 402 (`model_unavailable`) when credit is low. Answers and helper outputs are no longer comparable with phase 01–02 measurements; PROGRESS records both. The `standard` model reasons before answering, which adds hidden output tokens and latency to every general turn. With the offline profile the windows in `config/models.yaml` exceed Ollama's `num_ctx`, which silently truncates longer prompts; the smoke prompts are far shorter. Provider choice is left to OpenRouter, so quantization and latency can vary between calls.

### ADR-0066: One LiteLLM config; models, endpoints and keys come from the environment
- Date: 2026-10-05
- Phase: 02 (fix session for PR #2)
- Status: decided
- Context: ADR-0065 kept two LiteLLM configs (`config.yaml` on OpenRouter, `config.local.yaml` on Ollama) and a compose overlay to pick one. Switching profiles meant a `COMPOSE_FILE` incantation, the two files could drift apart, and a fresh clone without an OpenRouter key failed `make smoke`. The owner decided on one config whose values come from the environment, and asked to verify that LiteLLM resolves `os.environ/` in the `model` field before relying on it.
- Decision:
  - `infra/litellm/config.yaml` keeps the aliases `fast`, `standard`, `heavy`, `embed` and reads `model`, `api_base` and `api_key` from `FAST_MODEL`, `STANDARD_MODEL`, `HEAVY_MODEL`, `EMBED_MODEL`, `MODEL_API_BASE`, `MODEL_API_KEY` and `EMBED_API_BASE`. OpenRouter's `extra_body.reasoning.enabled` and `extra_body.provider.allow_fallbacks` come from `MODEL_REASONING_ENABLED` and `MODEL_PROVIDER_ALLOW_FALLBACKS` (both `false`). `docker-compose.yml` passes the variables, failing with a message when a required one is missing. `config.local.yaml` and `docker-compose.local.yml` are deleted.
  - `.env.example` holds the offline profile (Ollama on the host, no key), so a fresh clone passes `make smoke` without a network. The OpenRouter profile is five `.env` lines (three models, base URL, key), documented in README §21.4 and HANDOFF.
  - Verified on LiteLLM v1.100.0 with a capturing upstream: `os.environ/` resolves in `model` (the request went to the configured Ollama and OpenRouter models), in `api_base` and `api_key`, and inside nested `extra_body`; `false` arrives as a JSON boolean. A number arrives as a **string**: LiteLLM's `get_secret` converts only booleans, and Ollama rejects `"num_ctx": "32768"` (`option "num_ctx" must be of type integer`). So `num_ctx` stays a literal per alias, equal to the tier windows (ADR-0067); on OpenRouter it is sent as an extra field, which OpenRouter ignores (checked by the OpenRouter smoke run), and Ollama ignores `reasoning` and `provider`.
  - `scripts/preflight.sh` and `scripts/pull_models.sh` read the resolved values from `.env` through `scripts/models_env.sh`, check or pull only the `ollama/` and `ollama_chat/` models, and reach `host.docker.internal` as `localhost`. The shell variables `OLLAMA_HOST` and `OLLAMA_HOST_URL` are gone.
  - Model names now live in `.env` and `.env.example`, not in `infra/litellm/config.yaml`; code still names only roles and aliases (CLAUDE.md rule 4). This supersedes ADR-0029's "only file that names a model" and its host-side shell variables.
- Consequences: Switching profiles is a `.env` edit plus `make up`. An existing `.env` must gain the new variables (compose names the missing one). `num_ctx` is the one provider parameter that cannot move to the environment while LiteLLM resolves numbers as strings.

### ADR-0067: Tier windows and `max_output` are platform budgets, back to README §19.1
- Date: 2026-10-05
- Phase: 02 (fix session for PR #2)
- Status: decided
- Context: ADR-0065 set `window` and `max_output` to the smallest provider limits on OpenRouter (131072/8192, 256000/65536, 1000000/32768). Those are what a provider accepts, not what the platform budgets: the budget guard then never pressed, a 65536-token `max_tokens` failed with 402 on low credit, and the offline profile's `num_ctx` was far below the windows.
- Decision: `config/models.yaml` returns to README §19.1's budgets: `max_output` 2000/4000/8000 and `window` 32768/32768/65536 for `fast`/`standard`/`heavy`, the owner's values (the 32768 and 65536 windows match the offline `num_ctx`). Supersedes ADR-0065's tier values. `test_needs_more_fires_at_the_real_tier_budget` assembles a user message one budget long on `standard` (fits) and 500 tokens over it (`context.needs_more`, then `context_overflow`, since compressing never shortens the user message).
- Consequences: A provider must accept these values, which all three do. Long answers are cut at `max_output`; the answer reasons without hidden tokens on OpenRouter (`MODEL_REASONING_ENABLED=false`, ADR-0066), so 4000 is the visible answer budget.

### ADR-0068: User-derived text never enters the system role
- Date: 2026-10-05
- Phase: 02 (fix session for PR #2)
- Status: decided — Superseded by ADR-0076 on 2026-10-06 (the merge rule of `to_messages` only)
- Context: The summary block (a model's rewrite of the user's own messages) and the memory block (items extracted from them) were system-role blocks (`blocks.py`), merged into the system message. A user could thus place text in the system role of a later turn, or of another conversation through memory, by saying it once.
- Decision: User-derived text never enters the system role. The summary and memory blocks are user-role blocks, rendered by `context.summary@1.1.0` and `context.memory@1.1.0` inside `<conversation_summary>` and `<user_memory>` delimiters that call them data, not instructions. `ContextPackage.to_messages` merges adjacent blocks of one role into one message, so the summary joins the first window message and memory joins the current user message, and roles still alternate (some chat templates require it). The system message holds only system instructions and the state digest. Tested by `test_summary_and_memory_are_user_role_data_blocks_never_system`.
- Consequences: The rule binds later blocks too: when a node produces `decisions` or `open_threads` from user text, the state digest needs the same treatment (no node produces them yet). Retrieval, tool results and findings are untrusted tool data, governed by §10.3, and stay where they are in this session.

### ADR-0069: The agent's migration runner records a checksum per file
- Date: 2026-10-05
- Phase: 02 (fix session for PR #2)
- Status: decided
- Context: ADR-0055's runner recorded only a migration's name, so an edited, already-applied file was silently skipped and databases could differ while claiming the same version.
- Decision: `agent.schema_migrations` gains `checksum` (SHA-256 of the file). A new file is applied and recorded with its checksum; an applied file whose checksum differs fails startup with `MigrationChecksumMismatch` naming the file; a row recorded before checksums existed takes the current file as its baseline. Tested in `tests/integration/test_migrations.py`. Supersedes ADR-0055's migration record only.
- Consequences: A migration is never edited after it is applied; a change is a new file. The column is added in place on existing databases.

### ADR-0070: Agent integration tests run as the app role on a database provisioned by `infra/postgres/init`
- Date: 2026-10-05
- Phase: 02 (fix session for PR #2)
- Status: decided
- Context: ADR-0055 had the test fixture connect as the Postgres superuser and create and drop a database per session, so tests ran with privileges the service never has, and a permission the service lacks could pass in tests.
- Decision: `infra/postgres/init/10-agent-test.sh` creates `agent_test`, owned by `app`, with the `vector` extension; it is idempotent, and `make test-agent` runs it on an existing volume too. `agent-tests` gets `AGENT_TEST_DATABASE_URL` as the `app` role. The fixture empties every table in schema `agent` except the two migration records at the start of a session, instead of creating a database. Supersedes ADR-0055's test database only; the gateway's JDBC tests (ADR-0051) are unchanged.
- Consequences: The superuser password no longer reaches `agent-tests`. Two test sessions on one database at the same time would interfere. A migration edited during development fails the test database too (ADR-0069); dropping `agent_test` and re-running `make test-agent` resets it.

### ADR-0071: Retracting a reply deletes the memory extracted from that turn, in the same transaction
- Date: 2026-10-05
- Phase: 02 (fix session for PR #2)
- Status: decided
- Context: ADR-0058 left memory extracted from a reply that is later retracted (ADR-0048) until its user deleted it, so a withdrawn turn could still shape later answers.
- Decision: `TurnRunner.retract` sets the retract flag and deletes `memory_items` with `source_turn = turn_id` for the conversation's owner in one transaction: the flag is written through an `AsyncPostgresSaver` on that transaction's connection (the compiled graph copied with that checkpointer), and `MemoryStore.delete_from_turn` runs on the same connection. Either both commit or neither does. Idempotent. Tested: the turn's items go, older items and another user's row with the same `source_turn` stay, and a failure after the delete rolls back the flag too. Supersedes ADR-0058's handling of memory from a retracted reply only.
- Consequences: A retracted reply leaves no memory behind. A reply already folded into the summary stays there (ADR-0057's consequence, unchanged).

### ADR-0072: `model.call` records the model LiteLLM served
- Date: 2026-10-05
- Phase: 02 (fix session for PR #2)
- Status: decided
- Context: `gen_ai.response.model` was taken from the stream chunks, which LiteLLM fills with the alias (`fast`, `standard`), so traces could not say which model answered, least of all after a profile switch (ADR-0066).
- Decision: The gateway calls `with_raw_response` and records LiteLLM's `x-litellm-model-name` response header (the deployment the alias was routed to, e.g. `openrouter/qwen/qwen3.6-27b` or `ollama_chat/qwen2.5:14b-instruct`) as `gen_ai.response.model` and Langfuse's model name, with `model.served_known=true`. Without the header it records the alias and `model.served_known=false`. `tests/integration/test_model_span.py` asserts `prompts`, `tier_reason` and `gen_ai.response.model` on the answer's `model.call`.
- Consequences: The header names the configured deployment; which OpenRouter provider served the call is not exposed in a stream and is not recorded.

### ADR-0073: The rolling summary folds at the end of the turn; the turn-start fold is a fallback
- Date: 2026-10-05
- Phase: 02 (fix session for PR #2)
- Status: decided
- Context: ADR-0057 ran the fold in `assemble_context`, before routing, so a turn past the trigger waited for a `fast` call (4.15 s measured, PROGRESS) before its first token.
- Decision: A `fold_summary` node runs after `persist_state` and before `write_memory`, on the history including the turn just persisted, so the next turn starts with its summary ready. A failure there is recorded on the span and never fails the turn; the messages stay unfolded and are taken by the next fold. `assemble_context` keeps the fold as a fallback only when the history already exceeds `summary.trigger_tokens` at the start of a turn (`context.summary.lazy` on its span). Tests check that the end-of-turn summarizer call starts after the answer call ends, and that the fallback folds before the answer. Supersedes ADR-0057's placement only.
- Consequences: The fold no longer delays the first token; it delays `done` instead, like the memory write. A turn's answer sees the summary built at the end of the previous turn. When an end-of-turn fold fails and the history is under the trigger, the next turn's answer misses the messages that left the window until the following fold.

### ADR-0074: Evals run as a dedicated user with memory off
- Date: 2026-10-05
- Phase: 02 (fix session for PR #2)
- Status: decided
- Context: `make eval` ran as `analyst`, whose memory smoke writes and every case could read, so a case's answer depended on earlier runs (PROGRESS recorded 4 to 19 analyst rows across runs).
- Decision: The realm seeds `evaluator/evaluator` (base `user` role only, tenant `default`). `run_evals.py` logs in as `evaluator`, switches its memory off through the gateway (`PUT /v1/memory/settings`) and checks the switch before the first case. The analyst keeps memory for smoke.
- Consequences: Eval answers no longer depend on stored memory. The realm file changed: an existing Keycloak volume gets the user only after `make clean && make up` (or by adding it in the admin console).

### ADR-0075: Interpretation of rule 6 — a test may replace a real LiteLLM call's text
- Date: 2026-10-05
- Phase: 02 (fix session for PR #2)
- Status: decided
- Context: CLAUDE.md rule 6 sends every model call through Context Assembly and LiteLLM, tests included. Some defects of real models are not deterministic (script drift such as `الريyal`), and the mock LiteLLM returns one fixed text per alias, so a test cannot make a model produce them on demand.
- Decision: Accepted interpretation: a test may let a real call go through Context Assembly and LiteLLM and then replace the returned text, to simulate a non-deterministic model defect. The call itself is never skipped and no model endpoint is called directly.
- Consequences: Tests such as `tests/integration/test_answer_retry.py` stay within the rule. A test that never makes the LiteLLM call is still a violation.

### ADR-0076: A retracted exchange leaves the context as a pair; only the current message merges
- Date: 2026-10-06
- Phase: 02 (second fix session for PR #2)
- Status: decided — Superseded by ADR-0077 on 2026-10-06 (the summary's position only)
- Context: ADR-0048 skipped a retracted reply but kept the user message of its turn, and ADR-0068 had `ContextPackage.to_messages` merge every pair of adjacent blocks of one role. After a retract the window ended with that orphan question, so it merged with the memory block and the current message: the model saw `"A\n\nB"` as one user turn (review of PR #2, finding 1).
- Decision:
  - `context/messages.py` `visible()` marks, besides each retracted reply, the user message right before it, which `persist_state` stored with it. The sliding window and the summarizer's plan (`plan_summary`: the history count and the fold slice) read history through it, with pairs found on the whole history before the summary cursor or window slice applies. The orphan question never reaches a model either.
  - `to_messages` merges same-role blocks only within the current-message group (`summary`, `memory`, `user_message`) and among system blocks. A window message is never merged with a neighbour. A window that ends with a user message right before a group block raises `HistoryEndsWithUserMessage`, a core error, instead of merging.
  - Tested: a history ending in a retracted pair gives `[system, user "B"]`, and with memory hits the memory block joins `"B"` only; a summarizer slice ending on a retracted pair ends on the assistant message before it; the window skips the pair; `to_messages` raises on a window ending with a user message.
  - Supersedes ADR-0048's "The user message of that turn stays" and ADR-0068's merge rule ("the summary joins the first window message") only.
- Consequences: A summary block followed by a window that starts with a user message gives two consecutive user messages; roles no longer strictly alternate there. Whether every served model's chat template accepts that is not verified; one that requires strict alternation would need the summary moved into the current-message group, a new decision. A retracted question already folded into the summary stays there (ADR-0071's consequence, unchanged).

### ADR-0077: The summary block sits after the window, inside the current message
- Date: 2026-10-06
- Phase: 02 (second fix session for PR #2)
- Status: decided
- Context: ADR-0076 left the summary block before the window, so when the window started on a user message the model received two consecutive user messages, and whether every served model's chat template accepts that was not verified.
- Decision: The pipeline in `config/context.yaml` runs `sliding_window` before `rolling_summary`: system → state digest → history window → summary → memory → (system-role evidence blocks) → user message. The summary is thus adjacent to the memory block and the question and merges with them under ADR-0076's rule; it never precedes history. README §7 already lists the window (step 3) before the summary (step 4) and makes the order configuration, so the README is unchanged. Tested by `test_with_a_summary_the_messages_strictly_alternate_after_the_system_message` (with and without memory hits): after the system message the roles alternate user/assistant and end on the user. Supersedes ADR-0076's summary position and its alternation consequence only.
- Consequences: With a summary present, roles strictly alternate after the system message. The current user message carries `<conversation_summary>`, then `<user_memory>`, then the question. The summarizer node gets the same order: the fold slice, then the existing summary joined with its request prompt.

### ADR-0078: Guard policy scopes in phase 03; `role:` is reserved; MODEL_TIER policies are ceilings
- Date: 2026-10-06
- Phase: 03
- Status: decided — Superseded by ADR-0107 on 2026-10-06 (the refusal of role scopes only)
- Context: README §10.1 lists `global | tenant:{id} | role:{role} | kb:{slug} | tool:{name}`, narrower overriding wider, but the agent never sees roles (ADR-0046), so a role scope could not be resolved where policies run. Owner decision D2.
- Decision: Phase 03 supports `global`, `tenant:{id}`, `kb:{slug}` and `tool:{name}` (`tool:` names a tool or a capability, never a permission, ADR-0025). `role:{role}` stays in the vocabulary and its precedence slot and is refused by validation with an error that names the decision ("role scopes are reserved and not supported; the agent never sees roles"). Precedence, narrow over wide: tool > kb > role > tenant > global. MODEL_TIER policies are ceilings only: the final tier is at most min(`caps.default`, `principal.max_tier`, every applicable policy ceiling).
- Consequences: Role-based policy needs a design in which the gateway resolves roles, not the agent; until then role-specific behaviour is the capability permission `cap:{slug}` plus tenant policies. A MODEL_TIER policy can never raise a tier.

### ADR-0079: The answer streams through a DATA sentence buffer; it is buffered whole only for OUTPUT block or rewrite
- Date: 2026-10-06
- Phase: 03
- Status: decided — Superseded by ADR-0111 on 2026-10-07 (streaming mode adds a whole-answer pass only)
- Context: ADR-0039 (interim) streamed tokens unchecked and retracted on a late failure, with the full fix left to phase 03 as ADR-0031 proposed. Owner decision D3.
- Decision: DATA always runs on a sentence buffer (ADR-0089): a sentence leaves the agent only after DATA (target `answer`) ran on it, so a redaction happens before the text leaves. The whole answer is buffered before it streams only when an enabled OUTPUT policy with action `block` or `rewrite` applies to the principal's scope; then OUTPUT and DATA run on the whole answer and the new `release_answer` node streams the checked text a sentence per token event. `rewrite` exists only in buffered mode. `retract` remains only for checks no buffer can run early (`validate` on the complete answer). AgentState gains `answer_mode` (`streaming` | `buffered`). Time to first token was measured in both modes against the phase 02 baseline (PROGRESS, phase 03).
- Consequences: Streamed text is never unredacted; the stored reply and the shown reply are the same text, so ADR-0048's `shown_answer` audit copy no longer occurs in practice. The first token waits for the first complete sentence. An answer is buffered whole only where a block or rewrite policy needs it, which costs a full generation before the first token (measured: p50 4.53 s against 2.01 s streaming on five-sentence answers).

### ADR-0080: One-time purge of what was stored before the DATA guard
- Date: 2026-10-06
- Phase: 03
- Status: decided — Superseded by ADR-0112 on 2026-10-07
- Context: Phase 02 stored memory items and rolling summaries with no DATA guard (PROGRESS tech debt, PR #2 fix session). Owner decision D4.
- Decision: `make purge-pre-guard` (`python -m app.guardrails.purge`) checks every `memory_items` row as a DATA `memory_candidate` for its own user and tenant and deletes the rows the policies block, with the context packages that used them; it checks the summary in each conversation's latest checkpoint as a DATA `summary` and writes a redaction back in place through a state update under the conversation's turn lock (a block removes the summary). Checkpoint history and messages are not touched: development data is fake, and production starts with the guard. `DRY_RUN=1` prints the same counts and changes nothing; the command is idempotent. A fail-closed policy that cannot be evaluated stops the run.
- Consequences: Earlier checkpoints of a thread may still hold an unredacted summary; nothing reads them after the purge, and they go with the thread. The counts of the development run are in PROGRESS.

### ADR-0081: Retention: no memory, summary or extractor content in Langfuse; context packages expire and go with their memory items
- Date: 2026-10-06
- Phase: 03
- Status: decided — Superseded by ADR-0110 on 2026-10-07 (the content hashes and judge calls only)
- Context: Generation spans carried every model call's messages and output (ADR-0021), so memory items and summaries outlived a user's delete in Langfuse; `agent.context_packages` had no retention (ADR-0059). Owner decision D5.
- Decision: No memory, summary or extractor content goes to Langfuse: the `memory` and `summary` blocks of a captured package become a placeholder with the block's token count, a SHA-256 of its text and, for memory, the item ids; the `summarizer` and `extractor` calls carry no messages and no output, only token counts and content hashes (`input_sha256`, `output_sha256` on every model call). `agent.context_packages` rows expire `DEBUG_CONTEXT_RETENTION_DAYS` after they are recorded (default 7, `.env`; older rows count from creation), are never served after that and are deleted as new rows are written; each row records the memory item ids it used, and deleting a memory item (by its user, by a retract, or by the purge) deletes the packages that used it.
- Consequences: A user's delete of a memory item leaves no copy of its text in the agent's tables or traces. Debugging a summary or an extraction means reading Postgres while the item exists, not the trace. The user's own message and the answer are still captured as before (ADR-0021).

### ADR-0082: One turn at a time per conversation, under a Postgres advisory lock
- Date: 2026-10-06
- Phase: 03
- Status: decided — Superseded by ADR-0109 on 2026-10-07 (the order of refusals and the release only)
- Context: Two turns in flight on one thread each started from the checkpoint before the other, and the last writer won (PROGRESS, second PR #2 fix session). Owner decision D6, on the design recorded there.
- Decision: `TurnRunner` holds `pg_try_advisory_lock` keyed on the conversation id (the UUID's upper 64 bits) around the graph invocation and around `retract`, on a dedicated pool of `LOCK_POOL_SIZE` connections (`.env`, default 20), never the checkpointer's pool. The route takes the lease before it answers, so a second turn is refused with 409 `turn_in_flight` before any stream starts; the gateway returns 409 `turn_in_flight` and audits it as `denied`. The lease is released when the turn ends, fails or the client disconnects (the stream's end and the response's background task, idempotently); a retract waits up to 10 s for the turn it follows. Postgres releases a session lock when its connection ends. The code is in `config/messages/errors.yaml`, the gateway catalogs and the web `ar.json`/`en.json`.
- Consequences: At most `LOCK_POOL_SIZE` turns run at once across conversations in one agent process; more wait for a connection. Phase 08's resume must take the same lock.

### ADR-0083: The Arabic-name recognizer is seeded log_only
- Date: 2026-10-06
- Phase: 03
- Status: decided
- Context: The Arabic-name recognizer is a heuristic (a title followed by Arabic words, or a nasab "X بن Y") with false positives. Owner decision D7.
- Decision: It ships in its own seed policy, `pii-arabic-names-log` (DATA, all targets, presidio entity `ARABIC_NAME`), with action `log_only`, never `redact`.
- Consequences: Names are recorded in guard events and matches per day but never changed or blocked. The redacting policy cannot include the entity, which is why the name heuristic is a seventh seed policy beside the six of §10.4.

### ADR-0084: Guard thresholds and on_error defaults
- Date: 2026-10-06
- Phase: 03
- Status: decided
- Context: README §12 fails CI below `evals/thresholds.yaml`, and §10 did not say what happens when a checker cannot run. Owner decision D8.
- Decision: `evals/thresholds.yaml` holds, for the guardrails suite, 1.00 for the deterministic checkers (regex, presidio, keyword_list) and, for llm_judge, accuracy ≥ 0.85 with a false-positive rate ≤ 0.10. Each policy has `on_error`: `closed` is the default for regex, presidio and keyword_list (the turn ends with `guard_unavailable`); `open` is the default for llm_judge (allow and log).
- Consequences: A presidio-analyzer outage stops every turn that a presidio policy guards; a judge outage lets text through with a logged event. ADR-0099 records how `closed` reads after a reply is persisted.

### ADR-0085: Out of scope in phase 03
- Date: 2026-10-06
- Phase: 03
- Status: decided
- Context: README §10.2 lists more checkers and §10.4 more admin features than phase 03 builds. Owner decision D10.
- Decision: Not in phase 03: the `opa` and `classifier` checkers; `sql_static` and the `sql-tables-allowlist` seed (phase 07); the user's "this block was wrong" flag (phase 09); interrupt and resume for `require_approval` (phase 08: the broker ends the call with `approval_required`); asynchronous memory writing (phase 09); the `role:{role}` scope (ADR-0078).
- Consequences: The checker vocabulary is `regex | presidio | keyword_list | llm_judge | tier_ceiling` (ADR-0093) until those phases.

### ADR-0086: The agent owns guard policies; versions are immutable; seeding inserts absent ids only
- Date: 2026-10-06
- Phase: 03
- Status: decided
- Context: README §10.1 stores policies in PostgreSQL and §6.1 lists policy management among the gateway's responsibilities. Implementation choices I1 and I2.
- Decision: `agent.guard_policies` (migration `0002_guardrails.sql`) is owned by the agent; the gateway never writes it and proxies admin calls (the ADR-0059 pattern). A version is immutable: an edit inserts a new version row and makes it current. Seeding reads `config/policies/*.yaml` and every capability's `policies/` at startup and inserts only ids absent from the table; an existing policy is never overwritten, whatever its file says now.
- Consequences: After the first boot the admin UI owns a policy; editing its seed file changes nothing on an existing database (a new id does). Every evaluation names the exact version (`policy_id@version`) in state, spans, events and the audit delta.

### ADR-0087: A policy change reaches every agent process within 2 s through Redis pub/sub
- Date: 2026-10-06
- Phase: 03
- Status: decided
- Context: README §10.1: cached in Redis, hot-reloaded on change. Implementation choice I3.
- Decision: Each agent process keeps the enabled current versions in memory. A write goes to Postgres, the store rewrites the Redis key `{ns}:guard:policies` from Postgres and publishes on `{ns}:guard:policies:changed`; every process reloads from that key on the message. A process also reloads from Postgres when it (re)subscribes and every 30 s. `tests/integration/test_policy_store.py` asserts the 2 s bound with two stores on separate Redis connections.
- Consequences: The agent now depends on Redis (`REDIS_URL`); without it a process sees only its own changes until its next refresh. Tests use Redis database 15 and a namespace per session.

### ADR-0088: llm_judge verdicts are cached in Redis for 24 hours
- Date: 2026-10-06
- Phase: 03
- Status: decided — Superseded by ADR-0110 on 2026-10-07
- Context: README §10.2: the judge is cached by text hash. Implementation choice I4.
- Decision: The key is a SHA-256 of `policy_id@version` and the normalized text (`app/text/arabic.py` normalize), under `{ns}:guard:judge:`, with a 24 h time to live. A cache outage costs a model call, never the check.
- Consequences: Editing a judge policy never reuses an old verdict. Repeated eval runs within 24 h read cached verdicts unless the keys are flushed (PROGRESS records when they were).

### ADR-0089: The sentence buffer flushes on . ? ؟ ! ؛ or a newline, or at 240 characters
- Date: 2026-10-06
- Phase: 03
- Status: decided — Superseded by ADR-0111 on 2026-10-07
- Context: D3 needs a sentence boundary for DATA while streaming. Implementation choice I5; the details that keep PII whole are assumed.
- Decision: The terminators and the limit are in `config/guardrails.yaml` (`streaming`). A terminator ends a sentence only when followed by whitespace (so "3.5", "x.y@example.org" and "055.123.4567" stay whole); a newline always ends one; at 240 characters the cut goes to the last whitespace that is not between two digits, else at 240.
- Consequences: A number written in groups is checked whole; an abbreviation such as "د." followed by a name splits the name heuristic's span (log_only, ADR-0083). A PII value longer than 240 characters without spaces could still be split; none of the recognizers matches one.

### ADR-0090: The guardrails suite runs as admin through the gateway's policy test endpoint
- Date: 2026-10-06
- Phase: 03
- Status: decided
- Context: README §12: `guardrails.jsonl` maps text to the expected action per policy. Implementation choice I6.
- Decision: A guard case names `policy_id`, `text`, optional `checkpoint` and `target`, and `expect.action` (and `expect.entities`); `run_evals.py` runs it as the seed user `admin` through `POST /v1/admin/policies/test`, the runtime engine path, and records a Langfuse trace per case for the dataset run. The `general` suite stays on `evaluator` with memory off (ADR-0074).
- Consequences: The suite tests checkers, configs and the engine's decision for one policy at a time, enabled or not; combination across policies is covered by the agent's tests.

### ADR-0091: Agent and gateway API versions move to 1.3.0
- Date: 2026-10-06
- Phase: 03
- Status: decided — Superseded by ADR-0116 on 2026-10-07 (the `guard` event's fields only)
- Context: Phase 03 adds endpoints and fields to both specs. Implementation choice I7.
- Decision: One bump for the phase: agent OpenAPI 1.3.0 (admin policy endpoints, 409 on `/v1/turns` and on retract, `details` on errors) and gateway OpenAPI 1.3.0 (admin policy endpoints, 409 `turn_in_flight` on `/v1/chat/turns`, `details` on errors). `docs/events.schema.json` changes only additively (`guard` gains `policy_version`, `target`, `entities`, `latency_ms`; `error` gains `policy_id` and `guard_events`); its id stays `events.v1`. The URL prefix stays `/v1`.
- Consequences: The drift tests (ADR-0044) pin both documents at 1.3.0; the web types are regenerated.

### ADR-0092: Narrow over wide applies within a policy group; across groups the most restrictive action wins
- Date: 2026-10-06
- Phase: 03
- Status: assumed — Decided by the owner on 2026-10-06; see ADR-0105 for exceptions — Superseded by ADR-0108 on 2026-10-07 (the resolution rule)
- Context: README §10.1 says narrower scopes override wider ones but not which policies a narrower one overrides. Overriding every wider policy would let any tool-scoped warning cancel a global PII redaction.
- Decision: A policy has an optional `group`, by default its own id. Within one checkpoint evaluation, among the matched policies of one group only those at the narrowest scope count (tool > kb > role > tenant > global); an overridden match is recorded as `allow` with "overridden by id@version". Across groups the most restrictive action wins: block > require_approval > rewrite > redact > warn > log_only > allow, and the spans of every winning `redact` are replaced together. A block's message is the deciding policy's (highest severity, then narrowest scope). The seed PII policies are in groups `pii-output` and `pii-memory`, so a tenant exemption is a narrower policy of that group with action `allow`.
- Consequences: Overriding is explicit: an admin puts policies in one group to make one replace the other. Unrelated policies always all apply.

### ADR-0093: MODEL_TIER policies use the tier_ceiling checker with action log_only or warn
- Date: 2026-10-06
- Phase: 03
- Status: assumed — Accepted on 2026-10-06
- Context: Every policy names a checker (README §10.1), but none of the four phase 03 checkers reads a tier request, and a ceiling is not a block (README §19.3: a capped request is served at the ceiling).
- Decision: A fifth, deterministic checker `tier_ceiling` (config `max_tier`, optional model `roles`) matches when the requested tier is above `max_tier`; the engine then lowers the request's cap to the ceiling. MODEL_TIER policies must use it, only it may use MODEL_TIER, and their action is `log_only` or `warn`. The tier event's reason names the policy (`capped by policy id@version`); its evaluations are carried on the TierDecision and reported to the turn by `prepare_call`.
- Consequences: A tenant or capability ceiling is a policy, not a code change. The judge cannot run at MODEL_TIER, so resolving the judge's own tier can never recurse into the judge.

### ADR-0094: A policy's switch lives in a head row; switching never makes a version
- Date: 2026-10-06
- Phase: 03
- Status: assumed
- Context: I2 makes versions immutable, while §10.4 lists enabling apart from versioning.
- Decision: `agent.guard_policy_heads` holds each policy's current version, `enabled`, and who changed it last; enabling or disabling updates the head only. A new version and an import carry the switch they name.
- Consequences: `id@version` identifies content alone; the switch's history is in the gateway's audit log (`policy.enable`), not in versions.

### ADR-0095: OUTPUT redact also buffers; a late block on streamed text still retracts
- Date: 2026-10-06
- Phase: 03
- Status: assumed — Rejected by the owner on 2026-10-06; superseded by ADR-0106
- Context: D3 buffers for OUTPUT block or rewrite. An OUTPUT redaction applied after the text streamed would break "redaction happens before text leaves", and D3 did not consider it. A policy can also be switched on between the answer node and the OUTPUT node of a turn (hot reload), and a DATA block can match a later sentence after earlier ones left.
- Decision: An applicable OUTPUT policy with action `redact` buffers the answer as block and rewrite do. In streaming mode, a blocking OUTPUT verdict (only a mid-turn reload can bring one) and a DATA block or an unavailable policy mid-answer emit `retract` for the text already released, then end the turn with the policy's message.
- Consequences: Besides `validate`, retract remains only for these races and for a DATA policy that blocks answers; the seed policies never trigger it. A DATA block on answers is the admin's choice that accepts it; buffering for it would make every turn pay a full generation.

### ADR-0096: What an evaluation records, where
- Date: 2026-10-06
- Phase: 03
- Status: assumed — Superseded by ADR-0116 on 2026-10-07 (what guard events carry to a client, and the audit delta's fields)
- Context: C1 sends every evaluation to state, a span, a guard event and the audit delta; streaming DATA evaluates every sentence, and most evaluations match nothing.
- Decision: Each policy evaluation is a `guard.check` span (policy id@version, checker, scope, action, matched, latency) under one `guard.evaluate` span per checkpoint call. `state.guard_events` and the terminal event's `guard_events` hold every matched evaluation and one `allow` record per checkpoint call when nothing matched (the streamed answer reports one `allow` for all its sentences). Only evaluations whose action is not `allow` stream as `guard` events. `sanitize_untrusted` records a `log_only` note when it removed or truncated something. The gateway's audit record keeps the count and adds `guard_policies`: each policy that acted as `{checkpoint, policy: id@version, action, target}`, plus the deciding `policy_id` of a blocked turn. Matched evaluations of real turns go to `agent.guard_events` (no text) for matches per day; the test panel records nothing.
- Consequences: Guard chips show what acted, not every check. Memory's "a guard flagged the turn" rule (README §8) still reads every non-`allow`, non-`log_only` event.

### ADR-0097: presidio-analyzer runs the Saudi recognizers on Arabic-folded text, as language en
- Date: 2026-10-06
- Phase: 03
- Status: assumed
- Context: README §14 adds `presidio-analyzer` with custom recognizers; Presidio has no Arabic NLP model in the official image, and its patterns do not see Arabic-Indic digits, tatweel or tashkeel inside numbers.
- Decision: The image is pinned (`mcr.microsoft.com/presidio-analyzer:2.2.362`) with one worker, and `infra/presidio/recognizers.yaml` is its whole recognizer registry: SA_MOBILE (05…, +9665…, 009665…, 9665…, with separators), SA_NATIONAL_ID (ten digits starting 1 or 2, contiguous or 4-3-3), SA_IBAN (SA + 22 digits, grouped by four or not), EMAIL_ADDRESS and the ARABIC_NAME heuristic, all declared for `en`. The checker folds the text with an offset map before sending it (Arabic-Indic and Persian digits to ASCII, tatweel and tashkeel removed, letters unchanged) and maps the spans back; the policy's entity list and score threshold filter the answer. Checksums (IBAN mod 97, national ID) are not verified.
- Consequences: The service takes about 740 MiB, mostly the spaCy model no recognizer uses; a smaller image would need a build of our own. A format-valid number with a wrong checksum is still redacted, the safer error.

### ADR-0098: How regex and keyword_list match Arabic
- Date: 2026-10-06
- Phase: 03
- Status: assumed
- Context: README §10.2 asks for Arabic normalization and light stemming in keyword lists, and named groups in regex; rule 7 asks Arabic cases of every checker.
- Decision: `regex` matches, by default (`fold: true`), the folded text and the folded pattern (digits to ASCII, tashkeel and tatweel removed, alef, alef maqsura and taa marbuta folded; never case-folded, `ignore_case` is a flag), and maps spans back to the original text. `keyword_list` compares words: each word of the text and of a keyword is normalized (`app/text/arabic.py`) and expanded with one stripped prefix combination of و/ف, ب/ل and ال (and لل), keeping at least three letters; a keyword matches when every one of its words shares a form with consecutive words of the text. `allow` mode matches text outside the list.
- Consequences: Admins write patterns and keywords in plain forms. Suffixes and roots are not stemmed.

### ADR-0099: Fail-closed after a reply is persisted means "not stored"; a broker failure ends the turn
- Date: 2026-10-06
- Phase: 03
- Status: assumed — Accepted on 2026-10-06
- Context: D8's `closed` ends the turn with `guard_unavailable`. The memory writer and the end-of-turn fold run after the reply is persisted and streamed, and a tool policy fails inside a capability.
- Decision: At the `memory_candidate` and end-of-turn `summary` targets, a closed failure stores nothing and the turn completes, recorded on the span (the lazy fold before the answer ends the turn as D8 says). A blocked summary keeps the old summary and still moves the cursor, so the folded messages leave the context instead of being kept unguarded. At PRE_TOOL and POST_TOOL a closed failure denies the call with `guard_unavailable` and `run_capabilities` ends the turn with that code after the capability returns.
- Consequences: An outage never stores unchecked memory or summaries, and never shows an error after the answer was delivered.

### ADR-0100: A guard block shows the policy's own message
- Date: 2026-10-06
- Phase: 03
- Status: assumed
- Context: README §10.1: `block` returns the policy's `message_ar/en`; error events carried only `errors.yaml` texts and the web preferred its catalog.
- Decision: `TurnError` carries the deciding policy's messages and `policy_id@version`; the error event's `message_ar`/`message_en` are the policy's, and it names `policy_id`. New codes: `input_blocked`, `guard_unavailable`, `turn_in_flight` (and `policy_not_found`, `policy_exists`, `policy_invalid` for the admin API). The web shows the server message when the error names a policy, its own catalog otherwise.
- Consequences: An admin's wording reaches the user unchanged, in both languages; a block policy cannot be saved without both messages.

### ADR-0101: The admin policy API and its audit
- Date: 2026-10-06
- Phase: 03
- Status: assumed
- Context: C3 lists the operations; their shape and auditing were open.
- Decision: The agent serves `/v1/admin/policies` (list; get with versions; create; `/{id}/versions`; `/{id}/enabled`; `/test`; `/export`; `/import`; `/metrics?days=`) behind the internal token, with the admin in `X-User-Id`/`X-Tenant-Id` as the actor. `policy_invalid` errors carry `details` (the validation messages). The gateway serves the same paths, checks the `admin` role, proxies with the caller from the token and audits fail-closed (ADR-0042) as `policy.list|get|create|version|enable|test|export|import|metrics`; a non-admin is refused with 403 `forbidden` and audited as `denied`; a tested text never enters the audit log (its length does). Import is all or nothing: an absent id is created, a changed one versioned, a switch alone switched.
- Consequences: Policies cannot be deleted, only switched off; the smoke test and the TTFT measurement leave disabled policies behind (`smoke-live-block`, `ttft-buffered-output`).

### ADR-0102: The judge and the rewrite go through Context Assembly with prompt variables
- Date: 2026-10-06
- Phase: 03
- Status: assumed
- Context: Rule 6 sends every model call through Context Assembly; the judge needs the policy's topic, examples and the text, and a rewrite needs the violation explained.
- Decision: `AssemblyRequest` gains `prompt_vars`, passed to the node's system and request prompts. The judge runs as the `guard_judge` node of `config/context.yaml` (`guardrails.judge.system` with the admin's topic and examples in the system role, `guardrails.judge.request` with the text, delimited as data, in the user role, ADR-0068), on the `judge` role, in the prompt locale of the text's script; inside a turn it is accounted to the turn, from the test panel to a turn of its own. A rewrite re-prepares the answer with one policy hint rendered from `guardrails.rewrite`.
- Consequences: Judge and rewrite calls appear in traces and recorded packages like any other call.

### ADR-0103: The smoke turn asks for three sentences; the gateway prints JUnit totals
- Date: 2026-10-06
- Phase: 03
- Status: assumed
- Context: ADR-0045's smoke check requires three token events before `done`; with the sentence buffer a one-sentence answer is one token event. Rule G1 asks gateway counts from the JUnit XML.
- Decision: The first smoke question asks for an answer in three short sentences; the check is unchanged. The gateway's image `test` stage and the `gateway-db-tests` container print `JUNIT <label> tests=… passed=… failed=… errors=… skipped=…` from `build/test-results` (`services/gateway/junit-totals.sh`), whether the tests pass or not.
- Consequences: The smoke check still proves the answer streams in parts.

### ADR-0104: tool-visibility-by-role is a tenant policy on top of capability permissions
- Date: 2026-10-06
- Phase: 03
- Status: assumed — Rejected by the owner on 2026-10-06; superseded by ADR-0107
- Context: README §10.4 names a seed `tool-visibility-by-role.yaml`, but role scopes are refused (ADR-0078) and visibility by role is already the `cap:{slug}` permission the gateway derives from roles (ADR-0006).
- Decision: The seed expresses the policy layer with the D2 scopes: TOOL_LIST, scope `tenant:restricted`, keyword list `web`, `sql`, action `block`: that tenant sees neither capability whatever its users' roles. No seed user belongs to it.
- Consequences: The file name keeps the README's; its description says what it does.

### ADR-0105: Policy exceptions: a narrower policy may weaken a wider one of its group, and is flagged
- Date: 2026-10-06
- Phase: 03 (owner decisions on PR #3)
- Status: decided — Superseded by ADR-0108 on 2026-10-07
- Context: ADR-0092 lets a narrower policy of a group override wider ones. The owner decided it, with the rule that such a weakening is visible and accountable.
- Decision:
  - A narrower policy may weaken a wider one in the same group, by design. An exception applies only inside its own scope and group, never to another tenant, kb, role or tool (the engine's scope resolution, unchanged).
  - When a version is saved (create, new version, import, seed) the store computes what it overrides: every other policy's current version, on or off, of the same group, at a wider scope, sharing a checkpoint and, when DATA is the only shared checkpoint, a target. It records them on the version as `id@version` (`agent.guard_policies.overrides`, migration `0003_policy_exceptions.sql`) and sets `exception = true` when one of them has a stricter action.
  - Strictness: block > rewrite > redact > require_approval > warn > log_only > allow. The engine's combination across groups and between policy roles uses the same order (`ACTION_RANK`); it differs from ADR-0092's order only between require_approval and rewrite/redact, which never meet at one checkpoint (require_approval is PRE_TOOL only, rewrite OUTPUT only, redact text checkpoints only).
  - The admin API returns `overrides` and `exception` on every policy view and on the save response, and `POST /v1/admin/policies/overrides` previews them for a draft; the admin list shows an exception badge, and the form and the saved notice name the overridden policies or say "overrides nothing".
  - The gateway's audit record of a create or new version carries `exception` and `overrides`, with the actor, fail-closed (ADR-0042); a preview is audited as `policy.preview`.
- Consequences: The flag describes the moment of saving: a wider policy saved later does not re-flag older narrower versions. Versions saved before migration `0003` carry `overrides = []`, `exception = false`; none of the seed policies overrides another.

### ADR-0106: OUTPUT redact runs on the sentence buffer; only block and rewrite buffer the answer
- Date: 2026-10-06
- Phase: 03 (owner decisions on PR #3)
- Status: decided — Superseded by ADR-0108 on 2026-10-07 (how the per-sentence call resolves groups only)
- Context: ADR-0095 (assumed) made an OUTPUT redact policy buffer the answer whole. The owner rejected it.
- Decision: Full buffering happens only for an enabled OUTPUT policy with action block or rewrite (D3, ADR-0079). An OUTPUT redact policy runs per sentence on the stream, like DATA: each sentence goes through the OUTPUT policies whose action is `redact`, then DATA (target `answer`), before it is released. The whole-answer OUTPUT check in streaming mode skips redact policies, so a sentence is never redacted twice; in buffered mode OUTPUT runs whole, redact included. The rest of ADR-0095 stands as decided here: a blocking OUTPUT verdict that a mid-turn reload brings in after the text streamed, and a DATA block or an unavailable policy mid-answer, emit `retract` for the released text and end the turn with the policy's message. `GuardContext` gains `actions`, which restricts a call to policies with those actions.
- Consequences: An OUTPUT redaction costs one more engine call per sentence instead of a full generation before the first token.

### ADR-0107: Role scopes through policy roles; tool-visibility-by-role uses one
- Date: 2026-10-06
- Phase: 03 (owner decisions on PR #3)
- Status: decided — Superseded by ADR-0113 on 2026-10-07 (the gateway service account's grants only)
- Context: ADR-0078 refused `role:` scopes because the agent never sees roles (ADR-0046). ADR-0104 (assumed) expressed `tool-visibility-by-role` with a tenant `restricted`, which the owner rejected: tenants are not policy groups.
- Decision:
  - Keycloak realm roles prefixed `policy:` are policy-scope roles. They grant nothing; authorization stays `cap:{slug}` and `kb:{slug}:read` in the gateway (ADR-0006).
  - The gateway forwards to the agent only the caller's realm roles that start with `policy:` (and match `policy:[a-z0-9][a-z0-9_-]{0,63}`), as `principal.policy_roles` (full names, sorted, omitted when empty). It never forwards any other role, and builds the principal from the verified token only: a client's `policy_roles` field or header is ignored.
  - A policy with scope `role:{name}` (a slug) applies when `policy:{name}` is in `principal.policy_roles`. Precedence is unchanged: tool > kb > role > tenant > global. When a user holds two policy roles whose policies in one group conflict, the stricter action wins (the order of ADR-0105).
  - MODEL_TIER policies may be role-scoped; they stay ceilings: final tier ≤ min(`caps.default`, `principal.max_tier`, every applicable policy ceiling).
  - Validation accepts `role:{name}`. The admin form lists the realm's `policy:*` roles, read through the gateway's Keycloak Admin API client (`GET /v1/admin/policy-roles`, the `gateway` client's service account, `view-realm`), and warns, without refusing, when the named role does not exist. A Keycloak failure there is 502 `identity_unavailable`.
  - Realm: role `policy:pii-exempt`, assigned to the seed user `analyst`. No seed policy uses it, so seed behaviour is unchanged.
  - Replacing ADR-0104: the tenant `restricted` is removed everywhere; `tool-visibility-by-role.yaml` is TOOL_LIST, scope `role:no-sql`, hides the `sql` capability, disabled by default. A test asserts that no seed policy, test fixture or realm entry sets a tenant to serve a policy; the integration tests isolate their policies with policy roles instead of tenants.
  - Agent and gateway OpenAPI stay 1.3.0 (PR #3 is unmerged; ADR-0044, ADR-0046 reasoning); `docs/events.schema.json` is unchanged.
- Consequences: This supersedes ADR-0078's refusal of `role:` scopes and ADR-0046's "the agent never sees roles" for the `policy:` namespace only. A realm role named `policy:*` is now a policy selector: granting one to a user can weaken a policy for that user, which is why exceptions are flagged and audited (ADR-0105). An existing Keycloak volume gets `policy:pii-exempt` only after `make clean && make up` or through the admin console. A `role:` policy applies only to new tokens, since the roles are read from the token.

### ADR-0108: Exceptions by containment, per span; kb: and tool: may not weaken at OUTPUT or DATA before phase 04
- Date: 2026-10-07
- Phase: 03 (PR #3 review round 1, owner decision)
- Status: decided — Superseded by ADR-0115 on 2026-10-07 (whole-text against spans, and which wider policies a flag counts)
- Context: ADR-0092 let any policy at a narrower scope *kind* override every wider match of its group, and ADR-0105 flagged such weakenings at save time. Review round 1 showed the consequences: a `role:` or `kb:` exception overrode a stricter `tenant:` policy in every tenant; a narrower match cancelled the whole wider policy, including entities it never covered; a `kb:` or `tool:` exception unredacted the whole answer, whatever the source of the text; the per-sentence OUTPUT call dropped allow exceptions that buffered mode honoured; and the flag went stale when a wider policy was saved later.
- Decision:
  - Containment: a policy overrides another of its group only when its scope is contained by the other's. `global` contains every other scope; `tenant`, `role`, `kb` and `tool` scopes do not contain each other, nor does a scope override itself. Between policies of one group whose scopes do not contain each other the stricter action wins (block > rewrite > redact > require_approval > warn > log_only > allow, ADR-0105's order).
  - Per span: an exception cancels the wider policy only for the spans its own checker matched (a span is cancelled when one of the exception's spans overlaps it); for a `presidio` exception, only spans of the entity types it configures. Every other span of the wider policy keeps its action; the wider policy is overridden whole only when none is left, and is otherwise reported as "partly overridden by". An exception whose checker matched nothing overrides nothing. A checker that answers for the whole text (no spans: `llm_judge`, `tier_ceiling`) cancels a wider whole-text verdict and every span of a wider policy; spans never cancel a whole-text verdict (assumed).
  - `kb:` and `tool:` policies that would weaken a wider policy of their group at OUTPUT or DATA are refused by validation (`policy_invalid`, naming this ADR) until phase 04 attributes each part of an answer to its source. The refusal covers every save that would produce such a pair, whichever side is saved; a seed refuses only within the groups it adds to, so a startup never fails over what an admin saved.
  - Streaming and buffered modes resolve groups the same way: `GuardContext.actions` limits only what executes. A call that executes redact alone (the per-sentence OUTPUT call) evaluates every applicable policy of each group that holds a redact policy, resolves those groups in full, and then executes the redactions that stand.
  - The `overrides` and `exception` of each policy describe the current versions of its group and live on its head (`agent.guard_policy_heads`, migration `0004_policy_changes.sql`); they are recomputed for every policy of the group, on or off, in the transaction of each save, import, seed and enable or disable. Policy writes are serialized by a transaction-scoped advisory lock. A version row keeps what it overrode when it was saved.
  - Audit of truth: the agent writes `agent.guard_policy_changes` (`policy_id@version`, change `create|version|enable|disable|import|seed`, actor, `exception`, `overrides[]`) in the change's own transaction. The actor is `X-User-Id`; the seed's is `seed`. A change row records the policy acted on; a policy whose flag a later save of another policy changed shows the new flag on its head. `GET /v1/admin/policies/{id}/changes` (agent and gateway, audited as `policy.changes`) serves the log, which the admin page shows per policy. The gateway's audit record stays as it was (ADR-0059 pattern).
- Consequences: An exception can no longer leak across tenants, entity types or groups, and both answer modes give one outcome. Role and tenant policies of one group now combine by strictness, so a stricter tenant policy always wins over a role exception. Until phase 04, exceptions for a KB or a tool at the answer checkpoints are impossible; INPUT, TOOL_LIST, PRE_TOOL and POST_TOOL exceptions for them remain. Every policy change adds a change row by design.

### ADR-0109: The turn lock: ownership first, release before the end of the stream, a reset pool, 503 when busy
- Date: 2026-10-07
- Phase: 03 (PR #3 review round 1, owner decision)
- Status: decided
- Context: Review round 1 found that the route took the conversation's lock before checking ownership, that the stream's end cancelled a turn task still unlocking, that the lock pool returned connections without a reset, and that an exhausted pool ended in an unhandled `PoolTimeout` (500).
- Decision: The route binds or checks the conversation's owner before taking the lock; a non-owner gets 403 `conversation_forbidden` and never holds it. The turn task releases its lease before it signals the end of the stream, and the stream then waits for the task instead of cancelling it; only a consumer that leaves early cancels the task. The release runs once, in a task of its own that a cancellation cannot stop (`asyncio.shield`). The lock pool resets every returned connection with `pg_advisory_unlock_all()`. When no lock connection frees up within 5 s, the route answers 503 `service_busy` (and a retract 503 too); the gateway maps the agent's 403, 409 and 503 refusals to its own codes, auditing 403 and 409 as `denied` and 503 as `error`. `service_busy` is in `errors.yaml`, the gateway catalogs and the web `ar.json`/`en.json`. The APIs stay 1.3.0 (unmerged, CLAUDE.md).
- Consequences: A stranger who knows a conversation id cannot keep its owner out. No pooled connection can hold a conversation's lock, so session re-entrancy can never let two turns of one conversation in. Concurrent first turns of different users now see 403 for all but the first, never 409.

### ADR-0110: The judge's cache key and draft rules; content-free judge calls on stored text; keyed content hashes
- Date: 2026-10-07
- Phase: 03 (PR #3 review round 1, owner decision)
- Status: decided — Superseded by ADR-0116 on 2026-10-07 (the verdict cache key only)
- Context: A draft in the test panel had no version (`id@1`), so it read the stored version's verdict and wrote its own where live turns read; the key ignored the judge prompts and the model. A judge policy on a memory candidate or a summary sent that text to Langfuse. Content hashes were plain SHA-256, so short texts could be confirmed offline.
- Decision: The verdict key is a SHA-256 of the policy's id and version (for a draft, its id and a hash of its content), the judge prompts' `id@version` in the call's locale and the model alias the judge call resolves to for the principal, and the normalized text. The test panel never writes the cache; a draft never reads it. A judge call whose DATA target is `memory_candidate` or `summary` is content-free (`AssemblyRequest.content_free`): no messages or output in its trace, only token counts and hashes, and no recorded context package. Every content hash (the memory and summary placeholders, `input_hmac` and `output_hmac` of every model call) is HMAC-SHA256 under `CONTENT_HASH_KEY` (`.env`, generated by `make env`); the agent refuses to start without it.
- Consequences: An admin's test can never change a live verdict; editing a judge prompt or the alias behind the judge role starts a fresh cache. Existing development `.env` files need `CONTENT_HASH_KEY` added once.

### ADR-0111: The sentence buffer never cuts a joined number; a whole-answer pass catches what it missed
- Date: 2026-10-07
- Phase: 03 (PR #3 review round 1, owner decision)
- Status: decided
- Context: ADR-0089's forced cut could fall after a space at the very end of the buffer, before the next digits arrived, and between a digit group and a hyphen; a newline always cut; so a number could be checked in two halves and leave the agent unredacted.
- Decision: Neither a sentence cut nor the forced cut at 240 characters falls inside a digit run joined by whitespace (not a newline), hyphens and dashes, dots, commas, slashes or the Arabic separators ٫ ٬ ،, Arabic-Indic digits included; while such a run reaches the end of the buffer, the cut waits for the next chunk. A newline still always cuts. After the answer completes in streaming mode, DATA (target `answer`) runs once on the whole released text, and the whole-answer OUTPUT check executes its redactions: a span no sentence held whole makes the agent retract the released text and re-send the redacted text (the ADR-0039 path, retract reason `answer_redacted`), recorded on the node span (`data.final_pass`, `output.final_pass`: `clean` or `resent`); a block there retracts and ends the turn.
- Consequences: The redacted text replaces what was shown, a sentence per token event; the stored reply is the redacted one. A sentence such as "في عام 2024. 3 أشياء" stays one sentence. The whole-answer pass costs one more DATA evaluation per streamed turn.

### ADR-0112: The pre-guard purge runs under each conversation's lock, redacts memory in place and counts rows changed
- Date: 2026-10-07
- Phase: 03 (PR #3 review round 1, owner decision)
- Status: decided
- Context: ADR-0080 read a summary before taking the lock and wrote a redaction computed from that read; memory rows a `redact` policy matched were left unredacted; its counts were what it planned, not what it changed.
- Decision: Each thread's summary is read and written while the purge holds that conversation's lock. A memory row a policy blocks is deleted; one a policy redacts is rewritten in place with a vector of the redacted text; the context packages that used either go with its old text. The counts of a real run are the rows it changed; a dry run's are the rows it would change. The purge evaluates global and tenant policies only: it acts for no session, holds no policy role, and so a role exception never spares a stored row.
- Consequences: Running the purge while turns run is safe. A user exempt from PII redaction by a role still has stored PII purged.

### ADR-0113: The gateway service account's Keycloak grants
- Date: 2026-10-07
- Phase: 03 (PR #3 review round 1, owner decision)
- Status: decided
- Context: ADR-0107 said the `gateway` client's service account holds `view-realm`; the realm grants it `view-users`, `manage-users`, `view-realm` and `manage-realm`, as it has since before phase 03.
- Decision: The grants stay as they are: `view-users`, `manage-users`, `view-realm`, `manage-realm`. `view-realm` serves the policy-role listing (ADR-0107); the manage grants are kept for phase 05, where creating a knowledge base creates its Keycloak role `kb:{slug}:read` and assigns it. The policy-role listing filters by the same pattern the gateway forwards by and pages through every match.
- Consequences: The gateway's client secret can change the realm; it stays in `.env` only and is a secret of the highest class. Phase 05 may narrow the grants once its role management is built.

### ADR-0114: Review round 1, smaller choices
- Date: 2026-10-07
- Phase: 03 (PR #3 review round 1)
- Status: assumed
- Context: Decision 8 and 9 of the round 1 fixes left the form of some changes open.
- Decision: A checker's result carries `reason` and `reason_args` beside its English `explanation`; the test endpoint returns them as `explanation_key` and `explanation_args`, and the web renders `policies.explanation.{key}`, joining listed names with the locale's separator. The sanitize marker for a removed string is in `config/messages/sanitize.yaml` per locale and follows the turn's locale. The test panel takes an optional simulated principal (`tenant_id`, `policy_roles`), validated as a real principal and recorded in the gateway's audit detail. `guardrails.jsonl` drops both cases that repeat one of the judge policy's few-shot examples verbatim (the national day and the capital). The smoke run keeps the previous run's policy row counts in `build/smoke/policy-rows` (ignored by git). `measure_ttft.py` exits 2 when any turn failed.
- Consequences: The guardrails suite has 103 cases, 37 of them for the judge (8 at OUTPUT). The first smoke run on a machine records the reference counts and the second compares.

### ADR-0115: A whole-text exception never lifts spans; flags count enabled wider policies; induced flag changes are recorded
- Date: 2026-10-07
- Phase: 03 (PR #3, owner decisions before review round 2)
- Status: decided
- Context: ADR-0108 let a whole-text verdict (an `llm_judge` or `tier_ceiling` exception) cancel every span of a wider span-based policy, so a judge allow in group `pii` could lift a presidio redaction of a phone number. A change could also turn other policies into exceptions, or clear their flag, without any row naming them, and a flag computed over wider policies on or off stayed set after the wider one was switched off.
- Decision:
  - Whole text against spans: a checker is whole-text (`llm_judge`, `tier_ceiling`) or span-based (`regex`, `presidio`, `keyword_list`) by its type. A whole-text exception cancels only a whole-text verdict of a wider policy of its group; it never cancels span-based actions (redact or block). Span matches never cancel a wider whole-text verdict (ADR-0108's second half stands). Span-based exceptions cancel per span as in ADR-0108.
  - A whole-text policy that would weaken a span-based policy of its group is saved, with the warning `whole_text_cannot_lift_spans` ("this exception cannot lift span-based redaction") naming the span-based policies, in the save response and in the override preview the edit form shows. It is not refused.
  - Owner item: switching the wider policy off clears the exception it made. A policy's `overrides` and `exception` therefore count only the wider policies of its group that are enabled (its own switch does not matter). The refusal of `kb:` and `tool:` weakening at OUTPUT or DATA (ADR-0108) still counts wider policies on or off, since switching one on makes no new version to refuse.
  - Induced changes: when a save, import, seed, enable or disable sets or clears other policies' flags, the acting change row carries `induced` (`[{policy: id@version, exception}]`, migration `0005_induced_changes.sql`), and each affected policy gets a change row of its own, `flagged` or `unflagged`, with the acting change's actor, in the same transaction. A startup seed that recomputes flags under a newer rule writes those rows with the actor `seed`. The admin page shows them in each policy's change log.
- Consequences: A judge-based exception can exempt a principal from a judge-based block but never from PII redaction. Every change of a policy's exception flag has a row and an actor. Enabling a disabled wider policy flags its exceptions again, with a row.

### ADR-0116: The judge's cache is per tenant; a client's guard event says who decided what, never how
- Date: 2026-10-07
- Phase: 03 (PR #3 review round 2, owner decision)
- Status: decided — Superseded by ADR-0117 on 2026-10-07 (the test panel's cache reads only); Superseded by ADR-0118 on 2026-10-07 (its Consequences' claims only)
- Context: Review round 2 found a cross-tenant signal. ADR-0110 keyed a judge verdict by policy, judge prompts, model alias and normalized text, with no tenant, so one global judge policy shared one cache across every tenant. A cached verdict carried `, cached` in the checker's explanation and `_cached` in its reason, and every `guard` event and the terminal event's `guard_events` streamed that explanation (`detail`) to the client through the gateway's byte-for-byte relay. A user in tenant t2 who sent a guessed text saw whether anyone, in any tenant, had sent the same normalized text to that policy in the last 24 hours. Each event's `latency_ms` gave the same signal by timing.
- Decision:
  - **Cache key (supersedes ADR-0110's key only).** A verdict's key is a SHA-256 of the principal's `tenant_id`, the policy's id and version (for a draft, its id and a hash of its content), the judge prompts' `id@version` in the call's locale, the model alias the judge call resolves to for the principal, and the normalized text. The admin test panel uses the same key, with the simulated principal's tenant (the admin's own tenant when none is simulated), and still never writes; a draft still never reads. The rest of ADR-0110 stands.
  - **What a client sees.** A `guard` event, and every entry of a terminal `done` or `error` event's `guard_events`, carries `checkpoint`, `policy_id`, `policy_version`, `policy_name`, `action`, `target` (DATA only) and, for a block, the policy's `message_ar` and `message_en`. It never carries the checker's explanation (`detail`), reason, confidence, category, cache status, matched entity types or latency. `policy_version` and `target` are kept because they name the decision, not the check (A1, ADR-0096); `entities` and `latency_ms` go because they come from the check, and latency is a cache-status signal (assumed).
  - **Where the detail stays.** The `guard.check` span gains `guard.explanation`, `guard.reason`, `guard.score` and `guard.reason.{name}` (the judge's `category` and `confidence`). `state.guard_events` keeps each evaluation in full. The audit delta reaches the gateway as a new terminal-event field, `guard_audit` (every evaluation in full), which the gateway reads for the audit record and then removes before the terminal event leaves for the client. The terminal event is re-serialized with its `event` and `id` lines; one without `guard_audit` still leaves byte for byte. Each `guard_policies` entry of the audit record gains `detail`, `reason`, `reason_args` and `score`.
  - `docs/events.schema.json` keeps the id `events.v1`: `guard` loses `detail`, `entities` and `latency_ms` and gains `policy_name`, `message_ar` and `message_en`; `done` and `error` gain `guard_audit`, documented as agent to gateway only, never sent to a client. PR #3 is unmerged, so this follows the OpenAPI clause of CLAUDE.md by analogy (ADR-0046). The OpenAPI documents do not change. The web's chip shows the checkpoint and the policy's name, id, version, action and target only.
- Consequences: A tenant can no longer learn anything about another tenant's texts from the judge cache. What is left within one tenant: a cached verdict is still faster than a fresh one, so a turn's overall latency can tell a user that someone in their own tenant sent the same text to the same judge policy in the last 24 hours. One global judge policy now costs a model call per tenant per text. The audit record now holds the checker's explanation. For `keyword_list` that names the policy's configured keywords that matched; for `regex` and `presidio` it names pattern or entity types. It never holds free text from the message. A client that read `detail` or `entities` from a guard event (only this repo's web did) loses them.


### ADR-0117: The admin role is a platform operator; the test panel never touches the verdict cache
- Date: 2026-10-07
- Phase: 03 (PR #3 verification review of round 2, owner decision)
- Status: decided
- Context: The verification review of round 2 found that ADR-0116 left one cross-tenant signal. The admin test panel read the judge's verdict cache for a stored policy under the simulated principal's tenant, which the request names freely, and its result carried the cache marker (`, cached` in `explanation`, `_cached` in `explanation_key`). A holder of the `admin` realm role in tenant t1 could simulate t2 with a guessed text and learn whether anyone in t2 had sent it to that policy in the last 24 hours. Whether that is a leak depends on what `admin` is, which no ADR said.
- Decision:
  - **The `admin` realm role is a platform operator above all tenants.** It manages policies of every scope and may simulate any tenant in the test panel. A tenant-scoped administrator would be a separate role; v1 has none.
  - **The test panel never reads or writes the judge's verdict cache**, for a stored policy and a draft alike: every panel test calls the judge, so a panel result never carries a cache marker. Live turns keep the per-tenant cache of ADR-0116 unchanged.
- Consequences: A panel test of a judge policy always costs one judge call. Even a platform operator cannot use the panel to learn what a tenant's users sent. The `judge_*_cached` explanation keys of the web catalogs no longer appear in panel results; they remain for audit records.

### ADR-0118: What ADR-0116 closed and what remains: guard checks out of client usage, complete events only
- Date: 2026-10-07
- Phase: 03 (PR #3 verification review of round 2, owner decision)
- Status: decided
- Context: ADR-0116's Consequences said a tenant could no longer learn anything about another tenant's texts from the judge cache. The verification review found three gaps. The panel (ADR-0117). The judge's model call was added to the turn's usage, so `done.usage` (`tokens_in`, `tokens_out`) told a user, as an exact number, whether a verdict in their own tenant was cached. And `SseRelay` forwarded an event block cut off before its closing blank line as it arrived: a terminal event broken there reached the browser with its `guard_audit`, checker detail and cache marker included (reproduced with a stream that ends after the `data:` line).
- Decision:
  - **Guard checks are not turn usage.** A model call a guard checker makes (the judge's) goes through Context Assembly and LiteLLM as before (`call_model(..., account=False)`), but its tokens are not added to the turn usage a client receives in `done`. They are the check's `usage`: on the `guard.check` span (`guard.tokens_in`, `guard.tokens_out`) and on the check's record in `guard_audit`. A check that made a model call and matched nothing gets an audit-only `allow` record carrying its usage. The tier evaluations of a guard checker's own call (`prepare_call(..., audit_only=True)`: its MODEL_TIER records, which exist only when the judge ran) are audit-only too; this session's usage test found them in the client's `guard_events`, a second cache-status signal. An audit-only record is in state and in `guard_audit`, never streamed, never in `guard_events`; it never decides the streamed DATA `allow` record, and memory's "a guard flagged the turn" rule ignores it. The gateway's audit record keeps the turn's full usage (`usage` = the client's usage plus the guard checks') and adds `guard_usage`, the guard checks' sum, when any check called a model. The events schema gains `guard_audit[].usage`; its id stays `events.v1` (unmerged PR, ADR-0046 by analogy).
  - **The relay forwards complete events only.** On EOF or a broken upstream, an event block without its closing blank line is discarded, never forwarded; the client receives `agent_stream_interrupted` after the complete events that came before it.
  - **What is closed.** A user cannot learn from the judge cache whether a user in another tenant sent a text: the key is per tenant (ADR-0116) and the panel never reads it (ADR-0117). No client-visible field shows whether a verdict was cached: guard events carry no checker detail or latency (ADR-0116), client usage leaves guard checks out, and no partial event reaches a client.
  - **What remains.** Within one tenant, a cached verdict is faster than a fresh one, so a turn's overall latency can still tell a user that someone in their own tenant sent the same normalized text to the same judge policy in the last 24 hours.
- Consequences: `done.usage` no longer counts the judge; cost reports read the audit record's `usage` or `guard_usage`, not the client's. A turn cut off mid-event loses the partial event the client used to receive; it was never complete enough to render. This replaces only ADR-0116's Consequences claims; the rest of ADR-0116 stands.

### ADR-0119: The admin policy page in two languages: bilingual policy text, test policies, the meta line and Arabic shaping
- Date: 2026-10-07
- Phase: 03 (PR #3, the owner's visual check of /admin/policies)
- Status: decided (the owner's eight items); the points marked "assumed" are the agent's
- Context: The owner's visual check of `/admin/policies` found that versions saved before the change log existed (migration `0004`) showed no change row; that a policy had one name and one description in one language; that the meta line under a policy ran its items together with dot separators that reorder in Arabic; that the Arabic action labels were past-tense event words ("سُمح", "حُجبت بيانات") rather than names of actions; that the version history label ("الإصدار") was set in a monospace face; that smoke's and the TTFT script's policies filled the list; and that the policy roles placeholder mixed Arabic with a Latin example.
- Decision:
  - **Change log backfill.** Migration `0006_backfill_policy_changes.sql` inserts one `guard_policy_changes` row, `change = seed`, `actor = backfill`, for every policy version without a row, dated at the version's `created_at` and carrying the flags the version was saved with. A clean database has no such version (the seed writes its own rows), so there it adds nothing.
  - **Bilingual text.** A policy carries `name_ar`, `name_en` (both required, 1–200 characters), `description_ar` and `description_en` (both optional) in place of `name` and `description`, in the agent's policy model, the agent and gateway admin documents, import and export. Migration `0007_bilingual_policy_text.sql` renames the fields of every stored version (`name` → `name_en`, `description` → `description_en`); it changes no behaviour, so no version is added, and a version keeps its number. Assumed: a version saved before this has no Arabic name, so `name_ar` starts as a copy of the English one for an admin to replace, and `description_ar` starts empty; the page then shows the English description in its place. Every seed in `config/policies/` carries an Arabic name and description. The page shows the name and description of the UI locale, the other language only when that one is empty; the edit form edits all four.
  - **Assumed: guard events keep `policy_name`, the English name.** The client event contract (`docs/events.schema.json`) does not change; the chat chip's tooltip shows the English name.
  - **Test policies.** A policy has `purpose: production | test`, default `production`, part of the version's content. `scripts/smoke.sh` and `scripts/measure_ttft.py` import theirs as `test`. The admin list hides test policies until "إظهار سياسات الاختبار" / "Show test policies" is on (off on every page load; assumed). Nothing else treats them differently.
  - **The meta line.** Under each policy in the list, in the detail header and on each version history line: one chip per item, each in its own `<bdi>`, in this order in both locales: action, the exception badge when there is one, scope, checkpoints (one chip, joined with a Latin comma), checker, `id@version`. No separators between chips. The history line shows each version's own action and its exception flag as saved.
  - **Action labels.** `ar.json` names actions as nouns: allow سماح, log_only تسجيل فقط, warn تحذير, redact إخفاء, block حظر, rewrite إعادة صياغة, require_approval طلب موافقة, everywhere the UI shows an action (meta line, form, test panel, chat guard chips). `en.json` keeps its words.
  - **Arabic shaping.** No element sets letter-spacing or a text transform; a web test fails on any `tracking-*`, `uppercase`, `lowercase`, `capitalize`, `letter-spacing`, `text-transform` or `font-variant` in the web sources. A monospace face is used only for Latin identifiers (scope, checkpoints, checker, `id@version`, YAML, JSON), never on a label that can hold Arabic; the version history label is set in the sans face.
  - **Mixed text.** The policy roles field of the test panel has an Arabic-only placeholder in Arabic; the example `policy:pii-exempt policy:no-sql` is a hint line below it, inside `<bdi dir="ltr">`. Policy names, descriptions and the actor in the history and change log ("by …") render with `dir="auto"`.
  - **Assumed: component render tests.** The web's vitest compiles JSX (`oxc.jsx.runtime: automatic` in `vitest.config.ts`; `tsconfig.json` keeps `preserve` for Next) and renders components with `react-dom/server` in the node environment, with the app's own catalogs (`src/test/render.tsx`).
  - `docs/openapi/agent.json` and `docs/openapi/gateway.json` keep version 1.3.0: PR #3 is unmerged (ADR-0046).
- Consequences: An older database shows a `seed` row by `backfill` for each version that had none, and an Arabic name equal to the English one until an admin edits it; a clean one shows the seed's own Arabic. A policy document without `name_ar` or `name_en` is refused (`policy_invalid`, `name_ar: Field required`), on create, a new version and import, so an export from before this change must gain both names before it can be imported again. The first smoke or TTFT run on a database that holds their policies from before this change adds one version each (their content now carries Arabic names and `purpose: test`); later runs add none.

### ADR-0120: A national ID or iqama number needs a valid check digit or a context word
- Date: 2026-10-11
- Phase: 03 (PR #3, the owner's review of `config/policies/**` and `guardrails.jsonl`)
- Status: decided (the owner's item 2); the points marked "assumed" are the agent's
- Context: The `SaudiNationalIdRecognizer` (ADR-0097) matched every ten-digit number starting with 1 or 2. In Saudi text many such numbers are not IDs: transaction references that begin with the Hijri year (`1446…`), invoice and order numbers. All of them were redacted from answers and summaries and dropped from memory.
- Decision:
  - A `SA_NATIONAL_ID` candidate from presidio-analyzer is kept when its check digit is valid **or** a context word stands within the five tokens before it; without either it passes. Both checks run in the agent's presidio checker (`app/guardrails/checkers/saudi_id.py`) on the folded text the checker sends, so Arabic-Indic and Persian digits and grouped forms (`2345 678 904`, `2536-147-891`) are checked alike. The recognizer in `infra/presidio/recognizers.yaml` is unchanged and finds candidates; presidio's YAML recognizers cannot run a checksum, and their context enhancement needs an NLP model the analyzer does not load (ADR-0097).
  - **The check digit.** The Luhn scheme (ISO/IEC 7812-1, Annex B; H. P. Luhn, US patent 2,950,048) over all ten digits: from the right, every second digit, the check digit excluded, is doubled and reduced by 9 when above 9; the sum of all ten is a multiple of 10. Equivalently, from the left the digits in odd positions are doubled. Source: the National Information Center publishes no specification; the scheme is the one implemented by the community validators in common use, which agree with each other and on the test vector `1012345672` (valid) — `saudi-id-validator` on PyPI (https://pypi.org/project/saudi-id-validator/), the Drupal module `saudi_id_validator` (https://www.drupal.org/project/saudi_id_validator), and `arabicfmt`'s `validate_saudi_id` (https://app.unpkg.com/arabicfmt@0.1.3/files/dist/validate/index.d.ts). A unit test pins that vector.
  - **Context words.** هوية، الهوية الوطنية، سجل مدني، إقامة، ID، iqama, national ID. Tokens are runs of word characters, compared after `normalize` (alef forms, ة → ه, case folded). Assumed: an Arabic word matches with the prefixes و، ب، ل، ف، ال and their combinations and with a possessive ending (هويته، وهويته، إقامتي، سجله المدني); سجل matches only when followed by مدني or المدني, so سجل تجاري (a commercial register) is not context; "national ID" matches through its token `ID`. A context word is within the window when its last token is one of the five tokens before the number.
  - Assumed: the context words are code constants beside the check, not policy config: the entity's definition is the platform's, as the recognizer's pattern is.
- Consequences: A ten-digit number with a valid check digit is redacted anywhere, so roughly one random reference number in ten is still redacted when nothing names it; one that names an ID is redacted whatever its check digit, so a mistyped ID after «رقم الهوية» is still hidden. A context word more than five tokens back does not count (`صورة الهوية مرفقة، وأما رقم المعاملة لدى الإدارة فهو 1446012345` passes). Both seeds that use the entity (`pii-redact-output`, `pii-block-in-memory`) change behaviour with no new policy version, because the checker changed, not the policy. Fake numbers in tests and evals now carry valid check digits (G3: fake, never issued).

### ADR-0121: A block at TOOL_LIST removes the capability silently
- Date: 2026-10-11
- Phase: 03 (PR #3, the owner's review)
- Status: decided (the owner's item 3); the points marked "assumed" are the agent's
- Context: The owner asked for proof that `action: block` at TOOL_LIST removes the capability with no guard event to the client, no `message_ar`, and the turn going on with the remaining capabilities. It did not: the engine removed the capability and the turn went on, but the block record streamed to the client as a `guard` event with the policy's messages, sat in the terminal event's `guard_events`, and flagged the turn for memory like any block.
- Decision:
  - At TOOL_LIST the engine marks a block record `audit_only` and clears its `message_ar` and `message_en` (`engine._tool_list`). The record stays on the `guard.check` span, in the turn's `guard_audit` (the gateway's audit record) and in the audit delta; it is never streamed, never in `guard_events`, and does not flag the turn (ADR-0118's audit-only path). Other actions at TOOL_LIST are unchanged.
  - When every routed capability is removed the turn still ends with `no_capability_available` (unchanged).
  - `tool-visibility-by-role` uses the `regex` checker with `^sql$`, so only the slug `sql` is removed; `nosql_search` and `sql_reports` are kept (eval cases). Assumed: its `message_ar` and `message_en` stay, because a block policy must carry both (ADR-0100); they are never shown.
  - Assumed: the integration test routes a real turn to two capabilities with a second test-only capability `echo` (`tests/fixtures/capabilities/echo`, keyword routed, no tools), beside `probe` (ADR-0038); no core file names it.
- Consequences: A user whose role hides a capability sees no sign of it, as with a capability they lack the permission for (ADR-0006). The admin still finds the removal in the audit record and the trace. The test panel's TOOL_LIST verdict is unchanged (`block` when the slug is removed).

### ADR-0122: Gulf-dialect injection forms and the two topic policies in the Saudi government context
- Date: 2026-10-11
- Phase: 03 (PR #3, the owner's review)
- Status: decided (the owner's items 1, 4 and 5); the points marked "assumed" are the agent's
- Context: The owner's review found that `prompt-injection-input` missed Gulf forms (انسى folds to انسي, طنش, لا تلتزم ب…, possessive objects, اللي قبل / اللي فوق); that Arabic names and descriptions of seeds held Latin ids, checkpoints, roles and markers; and that the political topic was framed around parties and elections, which do not fit the Saudi government context.
- Decision:
  - `OVERRIDE_AR` matches a verb (تجاهل، انسى/انسي/انس، اهمل، طنش) followed by an optional جميع/كل and either a possessive object (تعليماتك، اوامرك، قواعدك، توجيهاتك, also with كم) on its own, or a bare object (التعليمات، الاوامر …) followed by السابقة، اعلاه، التي تلقيتها، اللي قبل or اللي فوق; or «لا تلتزم ب» with its object glued (بتعليماتك، بالتعليمات، بكل). Assumed: a bare object without a qualifier is not enough («تجاهل التعليمات المكتوبة على العلبة» passes), and «انسى» alone is ordinary speech («انسى الموضوع» passes).
  - Every seed's `name_ar`, `description_ar` and `message_ar` are Arabic words only; a unit test (`tests/unit/test_policy_seeds.py`) fails on any Latin letter in them.
  - `topics-politics-llm-judge` takes the owner's text (name الآراء السياسية, the topic description and five examples). New seed `topics-sectarian-tribal-llm-judge`, disabled, global, INPUT and OUTPUT, block, with the owner's name, topic description and message; its four few-shot examples are the agent's, and no eval case repeats any example of either policy.
  - A seed never overwrites a stored policy (I2): the changed seeds (`prompt-injection-input`, `tool-visibility-by-role`, `topics-politics-llm-judge`, `pii-redact-output`, `pii-block-in-memory`, `web-domain-denylist`) reach an existing database only as new versions, imported through `/v1/admin/policies/import` by the admin; a clean database gets them as version 1. The new seed is inserted on start because its id is absent.
- Consequences: The guardrails suite's judge cases measure the new definitions; thresholds are unchanged (`evals/thresholds.yaml`). Enabling either topic policy buffers every answer in scope (D3). An imported version keeps the seed's `enabled` value, so the two topic policies stay off.

### ADR-0123: The admin policy page, second visual check: actor names, scope fields, readable and structured configuration
- Date: 2026-10-11
- Phase: 03 (PR #3, UI session 2, the owner's visual check of /admin/policies)
- Status: decided (the owner's eight items; the structured editors are the owner's decision to land before merge); the points marked "assumed" are the agent's
- Context: The version history and change log named an admin by Keycloak subject id. The scope field was one free-text input with a long hint that mixed Latin tokens into Arabic sentences, as did the group hint. The detail view showed a policy's configuration as raw JSON, and the edit form edited it as raw JSON. A long policy name was cut off in the list with no way to read it. The daily matches table listed test policies the list hid. The test panel headed every resulting text "النص بعد الحجب", showed entity types as identifiers, and joined the explanation's values into one string. The import field's placeholder named the format inside an Arabic sentence.
- Decision:
  - **Actor names.** Migration `0008_actor_names.sql` adds `created_by_username` and `created_by_name` to `agent.guard_policies` (version rows) and `actor_username` and `actor_name` to `agent.guard_policy_changes`, beside the id, as they were at the time of the change. The gateway sends them on every admin call from the verified token: `X-User-Name` (`preferred_username`) and `X-User-Display-Name` (`name`, else `preferred_username`, as `GatewayPrincipal.displayName`), percent-encoded UTF-8, because a header value is ASCII and a display name may be Arabic. The agent decodes them; a blank or missing header is no name; a name is cut at 256 characters. Rows written by the seed carry `seed` in all three fields, and the migration gives every existing `seed` and `backfill` row (0006) the same word as username and display name. Other rows saved before this keep no name, and the page shows their id. The API adds the four fields, optional, to `PolicyView`, `PolicyVersionView` and `PolicyChange`; `docs/openapi/*.json` stay at 1.3.0 (unmerged PR, ADR-0046). The history and change log show the display name, with "اسم المستخدم: …، المعرّف: …" in a tooltip. Assumed: `agent.guard_policy_heads.updated_by` keeps the id only (the owner named the change and version rows).
  - **Scope and group fields.** The scope is a kind (عام، جهة، دور، قاعدة معرفة، أداة) and a value, written back as `global` or `{kind}:{value}`. Global has no value; a role is chosen from the gateway's `policy:*` list (`/v1/admin/policy-roles`), and a stored role the list no longer holds stays selectable with the existing warning; a tenant starts as `default`; changing the kind resets the value. Each hint is one short sentence of the locale, then each example id in its own `<bdi>` chip. Assumed: the examples are `default` (tenant), `moe` (knowledge base), `web` and `sql` (tool), and `pii-output`, `pii-memory` (group, the seeds' own groups). The unknown-role warning no longer names Keycloak inside the Arabic sentence.
  - **Readable configuration.** The detail view shows the configuration per checker with the locale's labels: keywords and the matching mode; patterns (name and pattern, as identifiers) and folding; entities by their Arabic labels and the score threshold; the topic, its examples (text, يُحجب / يُسمح) and the minimum confidence; the tier and model roles; DATA targets (الإجابة، الملخص، عناصر الذاكرة). An omitted threshold shows the engine's default, marked as such; an entity, tier or role the catalog does not know shows as its identifier in monospace. The raw JSON is behind "عرض الإعدادات التقنية", closed each time a policy opens.
  - **Truncated names.** A list name carries the full name in its `title`; the detail header wraps a long name instead of cutting it.
  - **Structured editors.** The form edits the configuration per checker: keyword tags; pattern rows (name, pattern) with add and remove; entity checkboxes with Arabic labels (رقم جوال سعودي، رقم هوية أو إقامة، آيبان سعودي، بريد إلكتروني، اسم شخص عربي) and the score threshold; the topic, an examples table and the minimum confidence; the tier and the model roles; DATA target checkboxes in every editor. "إعدادات متقدمة" shows a JSON view. The JSON text is the one state both views edit, so they cannot drift: an editor writes only the fields it shows, with every other key kept, so a config loaded and saved unchanged is identical; while the text is not a JSON object the editor is paused, the JSON view is shown, and saving is refused with an Arabic message. Before saving the editor refuses what the engine's validator would refuse (no keyword, no pattern, a pattern name outside `[A-Za-z0-9_]{1,64}`, a pattern that does not compile, no entity, a threshold outside 0–1, no topic, an empty example, no tier, no DATA target), each message from i18n; the agent's validator decides everything else. Assumed:
    - A pattern is compiled by the browser's engine with Python's named groups and backreferences rewritten (`(?P<n>`, `(?P=n)`); a pattern only Python accepts in some other way is refused in the form and must be rewritten. Every seed pattern compiles.
    - The tiers and model roles offered are those of `config/models.yaml`, listed as constants in the web; a stored value outside them is kept and shown.
    - Absent `targets` (or `roles`) means all, so every box is ticked; a change writes the explicit list.
    - Choosing another checker starts its config from a template (an empty list, or `standard` for a ceiling), keeping the DATA targets.
    - The web's tests read `config/policies/*.yaml` with the `yaml` package (2.8.4, a dev dependency) through a `config` build context (`make test-web`, the Dockerfile's test stage); no seed uses `keyword_list` or `tier_ceiling`, so those two editors round-trip a config written in the test.
  - **Daily matches.** The page owns "إظهار سياسات الاختبار"; the matches table hides the rows of test policies while it is off, as the list does.
  - **Test panel.** The heading over the resulting text follows the action (`policies.test.outputBy.*`: النص بعد الإخفاء، النص بعد الحظر، النص بعد التحذير، …). Entity types in the explanation and on matched-span badges use the presidio editor's Arabic labels; a pattern's name stays an identifier. The explanation sentences carry `<v>` around each value and `<list/>` for the matched items, so each technical value is rendered in its own `<bdi>`.
  - **Import.** The placeholder is "الصق هنا ملف السياسات"; the format name, YAML, is a chip in the section heading. Not changed: the export button's label.
- Consequences: A change made after this names its admin in the page; older rows by an admin show the id until they age out of view. An admin edits configuration without JSON, and the JSON view stays available for keys no editor shows (`mode`, `fold`, `ignore_case`, `language`). A config the editor refuses never reaches the agent; the agent still validates what it receives. `make test-web` needs the `config` build context.

### ADR-0124: DATA targets on the meta line, "prevent saving" for a memory block, checkpoint tooltips
- Date: 2026-10-11
- Phase: 03 (PR #3, UI session 2, the owner's item 9)
- Status: decided (the owner's item 9); the points marked "assumed" are the agent's
- Context: The meta line (ADR-0119) did not say which DATA targets a policy guards, so `pii-redact-output` and `pii-block-in-memory` looked alike. A block whose only target is a memory candidate does not refuse the reply; it keeps the candidate out of memory, yet it read "حظر". The checkpoints chip showed identifiers only.
- Decision:
  - For a policy with DATA among its checkpoints, target chips follow the checkpoints chip: one per target, in the order الإجابة، الملخص، عناصر الذاكرة. A policy that covers every target (no `targets`, or all three) gets one chip, "كل الوجهات"; the configuration summary and editor use the same words. A policy without DATA gets no target chip.
  - The action chip reads "منع الحفظ" ("Prevent saving") instead of "حظر" when the action is block, every checkpoint is DATA and the only target is `memory_candidate`: in the list, the detail header, each version line (each version by its own content) and the test panel's result. Assumed: a policy that also has another checkpoint keeps "حظر", because it blocks there.
  - The checkpoints chip has a tooltip: the descriptions of its checkpoints from the locale's catalog (رسالة المستخدم، القدرات المتاحة، قبل الأداة، بعد الأداة، الإجابة، البيانات الخارجة، سقف النموذج), joined with the locale's comma. Assumed: the chip stays one chip for all checkpoints (ADR-0119), so its tooltip lists each one's description.
- Consequences: The meta line is longer for DATA policies by one to three chips. The guard events, the chat chips and the API are unchanged: the action is still `block`, and only its label changes.

### ADR-0125: Uploaded files expire 30 days after the conversation's last turn; a daily purge deletes them
- Date: 2026-10-11
- Phase: 04 (owner decision D1)
- Status: decided
- Context: README §9.3 stores uploads in a session-scoped collection with `expires_at` but names no lifetime.
- Decision: An uploaded file and everything derived from it (the MinIO object in bucket `uploads`, its chunks in `agent.rag_chunks`, its attachment record) expire `retention_days` (30, `config/docs.yaml`) after the conversation's last turn. An upload sets the expiry from its own time; every turn of the conversation moves the expiry of all its attachments and chunks forward. A daily purge job in the agent deletes, for every expired attachment, the object, the chunks and the record, in that order; it is idempotent, so a crash between steps is finished by the next run.
- Consequences: A conversation that is never resumed loses its files after 30 days; a resumed one keeps them. Retrieval never returns an expired chunk, even before the purge runs.

### ADR-0126: Upload limits: 20 MB per file, 10 files per conversation, 300 pages per PDF, five types; checked in the gateway
- Date: 2026-10-11
- Phase: 04 (owner decision D2)
- Status: decided
- Context: README §6.1.3 puts upload size and type limits in the gateway.
- Decision: `config/docs.yaml` holds `limits`: `max_file_bytes` 20 MB, `max_files_per_conversation` 10, `max_pdf_pages` 300, types PDF, DOCX, XLSX, TXT, MD (by extension and content signature). The gateway refuses before anything is stored: 413 `file_too_large`, 415 `file_type_unsupported`, 413 `too_many_pages`, 409 `attachment_limit`, each with an Arabic and an English message. The agent re-checks the file count atomically when it registers the attachment, and the ingestion worker re-checks pages and type on the bytes it reads (defense in depth).
- Consequences: The gateway counts PDF pages itself (Apache PDFBox) and reads the same file as the agent, so the two limits cannot drift.

### ADR-0127: PII in a user's own uploads is redacted in answers like any other text
- Date: 2026-10-11
- Phase: 04 (owner decision D3)
- Status: decided
- Context: An uploaded CV or circular can hold national IDs, mobile numbers and IBANs.
- Decision: No docs-specific exception: the DATA policies apply unchanged. The model may read the document's PII (it is in the evidence it receives); the answer never shows it, because DATA redacts the answer before any sentence leaves the agent (ADR-0079, ADR-0111).
- Consequences: A user who asks "what is the national ID in my CV?" gets `[REDACTED:SA_NATIONAL_ID]`. A policy role exception (ADR-0107) still applies to its holder, as for any answer.

### ADR-0128: A scanned PDF fails with `scanned_unsupported`; OCR is v2 behind an Extractor hook
- Date: 2026-10-11
- Phase: 04 (owner decision D4)
- Status: decided
- Context: README §17 keeps OCR out of v1.
- Decision: A PDF whose pages carry no text layer ends as `failed` with reason `scanned_unsupported` and an Arabic message in the UI. Extraction is behind an `Extractor` protocol keyed by type, so an OCR extractor can be registered in v2 without touching the pipeline.
- Consequences: Image-only documents cannot be asked about in v1; the user is told why.

### ADR-0129: Uploads go through the gateway into MinIO; every attachment operation is audited fail-closed
- Date: 2026-10-11
- Phase: 04 (implementation choice I1)
- Status: decided
- Context: README §6.1.5 mentions pre-signed uploads; limits, ownership and audit must be enforced server-side and MinIO stays internal.
- Decision: The browser posts a multipart file to the gateway, which checks limits and ownership, stores the bytes in bucket `uploads` and registers the attachment with the agent, which owns the record (the ADR-0086 pattern). Upload, list, status read, download and delete are audited fail-closed (ADR-0042): refused with 503 `audit_unavailable` when the log is unreachable, answered only after the record is written. No browser ever talks to MinIO. This supersedes the "pre-signed" wording of README §6.1.5.
- Consequences: Upload bytes pass through the gateway's JVM; 20 MB per file keeps that bounded. A status poll every 2 s writes one audit row each time.

### ADR-0130: Ingestion runs in a background worker inside the agent, on a Redis queue
- Date: 2026-10-11
- Phase: 04 (implementation choice I2)
- Status: decided
- Context: README §9.3 describes an ingestion job; extraction, embedding and indexing take seconds to minutes.
- Decision: The capability-agnostic pipeline lives in `app/rag/ingest/`. Registering an attachment enqueues its id on a Redis list; worker tasks in the agent process take jobs, extract, normalize, chunk, embed and write the chunks. A job is idempotent by attachment id (it replaces that attachment's chunks in one transaction and then sets the status) and resumes after a crash: at startup every `pending` attachment is enqueued again. A failure sets `failed` with a reason code.
- Consequences: Ingestion competes with turns for the agent's CPU; the worker count is configuration. Phase 05 reuses the pipeline for KB documents.

### ADR-0131: One chunk table, `agent.rag_chunks`, behind the VectorStore interface
- Date: 2026-10-11
- Phase: 04 (implementation choice I3)
- Status: decided
- Context: README §2 puts vectors in pgvector behind a `VectorStore` interface with per-collection isolation.
- Decision: `agent.rag_chunks (collection, attachment_id, user_id, tenant_id, page, section, ord, text, text_norm, tsv, embedding, expires_at)`; `collection` is `sess_{conversation_id}` now and `kb_{slug}` in phase 05. Every query names the collection and the owner. `VectorStore` (`app/rag/vectorstore/`) is the only reader and writer.
- Consequences: Swapping to another vector database is one new `VectorStore` implementation.

### ADR-0132: Full text is a `simple` tsvector over Arabic-normalized text
- Date: 2026-10-11
- Phase: 04 (implementation choice I4)
- Status: decided
- Context: Postgres has no Arabic text search configuration.
- Decision: `text_norm` is the chunk normalized by `app/text/arabic.py` (alef, yaa and taa marbuta folding, tatweel and tashkeel removed, light prefix stripping); `tsv` is `to_tsvector('simple', text_norm)`. Queries are normalized by the same function, so Arabic matching is deterministic.
- Consequences: Suffixes and roots are not stemmed (ADR-0098's limit); the vector half of the hybrid search covers morphology.

### ADR-0133: Hybrid retrieval: vector top 30 and text top 30, reciprocal rank fusion, rerank, top 8
- Date: 2026-10-11
- Phase: 04 (implementation choice I5)
- Status: decided
- Context: README §7 step 6 and §9.3.
- Decision: Values in `config/docs.yaml`: 30 by vector similarity, 30 by full-text rank, fused by reciprocal rank fusion, reranked, the top 8 kept; when the evidence exceeds its budget, each chunk is compressed extractively (sentences sharing the most words with the question, README §7 step 6).
- Consequences: One retrieval costs one query embedding, two SQL queries and one rerank call.

### ADR-0134: The reranker is `bge-reranker-v2-m3` on Infinity behind the LiteLLM `rerank` alias
- Date: 2026-10-11
- Phase: 04 (implementation choice I6)
- Status: decided
- Context: ADR-0008 left serving the reranker to phase 04: Ollama has no rerank API. I6 asks for Infinity or Hugging Face TEI, whichever the pinned LiteLLM supports, with weights fetched by `make models`, never at container start.
- Decision: LiteLLM v1.100.0 supports both (`infinity/` and `huggingface/` rerank providers). TEI publishes CPU images for amd64 only, so on an arm64 host it runs emulated; Infinity `michaelf34/infinity:0.0.75` is the newest tag published for both amd64 and arm64. The `rerank` service runs that image with the model loaded from the volume `rerank-models` (`HF_HUB_OFFLINE=1`); `make models` downloads `BAAI/bge-reranker-v2-m3` into the volume. The LiteLLM alias `rerank` points at it with the `infinity/` provider. A rerank call, like an embedding, is not a chat call and takes no ContextPackage (ADR-0058 precedent). Its latency on the development host is recorded in PROGRESS.
- Consequences: About 2.3 GB of weights in a volume and one more container (CPU inference). A newer Infinity tag that is amd64-only would need emulation on arm64 hosts.

### ADR-0135: Extraction per type; Arabic presentation forms and visual order restored; recursive chunking 800/120
- Date: 2026-10-11
- Phase: 04 (implementation choice I7)
- Status: decided
- Context: README §9.3; Arabic PDFs often extract as presentation forms in visual (reversed) order.
- Decision: PDF per page with `pypdf` (BSD); NFKC folds presentation forms; a line detected as visual-order Arabic is reversed back to logical order (digit and Latin runs keep their direction). DOCX split by headings (`python-docx`, MIT); XLSX per sheet with the header row repeated in every chunk (`openpyxl`, MIT); TXT and MD heading-aware. Chunking per README §9.3: recursive, 800 tokens, 120 overlap, never across a page (PDF) or section (DOCX, MD) boundary, so every chunk has one page or section for its citation.
- Consequences: Visual-order detection is a heuristic with Arabic tests; a mis-detected line stays searchable by its words through the vector half.

### ADR-0136: Retrieval is a tool of the docs capability; chunks are untrusted data
- Date: 2026-10-11
- Phase: 04 (implementation choice I8)
- Status: decided
- Context: README §10.3: everything a tool returns is data.
- Decision: The docs capability retrieves through its tool `docs_search`, executed by the broker, so chunks pass POST_TOOL with `sanitize_untrusted` first. The capability renders them for the answer inside `<untrusted>` delimiters from its own versioned prompt files. A document that says "ignore your instructions" is data.
- Consequences: An instruction-like sentence in a document is replaced by the sanitize marker before the model sees it, so a document cannot instruct the answer.

### ADR-0137: Retrieval spans carry ids, pages, scores and token counts, never chunk text
- Date: 2026-10-11
- Phase: 04 (implementation choice I9; D5 precedent, ADR-0081)
- Status: decided
- Context: Uploaded content must not be copied into traces.
- Decision: The `rag.retrieve` span records attachment ids, chunk ids, pages, scores and token counts; ingestion spans record counts and reason codes. No span attribute holds document text.
- Consequences: Debugging retrieval quality uses ids and pages against the owner's own file, not the trace.

### ADR-0138: Attachment status is pending, indexed or failed; the web polls it every 2 s
- Date: 2026-10-11
- Phase: 04 (implementation choice I10)
- Status: decided
- Context: README §9.3: status streamed to the UI.
- Decision: The attachment record holds `pending | indexed | failed` and, when failed, a reason code. The web polls the status through the gateway every 2 s until it is terminal, and keeps the send button disabled while an attached file is pending.
- Consequences: No server push for ingestion progress; a poll is one audited request.

### ADR-0139: Routing for documents: the attachments rule, then the router model on later turns
- Date: 2026-10-11
- Phase: 04 (implementation choice I11; ADR-0015)
- Status: decided
- Context: ADR-0015 brought the router model with the second capability.
- Decision: The `attachments` rule selects `docs` on the turn a file is attached. On later turns of a conversation with indexed files, the router model (role `router`, fast tier) chooses between `docs` and `general`; its decision is cached by hash for 1 hour. `evals/golden/routing.jsonl` starts with at least 30 docs/general cases, Arabic and Gulf included; threshold 0.90.
- Consequences: A later turn in a document conversation costs one fast call, or none on a cache hit.

### ADR-0140: Citations are file name and pages, attributed after the answer by shared word n-grams
- Date: 2026-10-11
- Phase: 04 (owner decision on the G5 stop; replaces I12 and the preview panel of D2)
- Status: decided
- Context: I12 asked for citations naming chunk ids, a check that fails an answer citing a chunk not retrieved, and a preview panel. Each needed markers in the answer and a change to `answer.system`; the owner chose a simpler design when the session stopped under G5.
- Decision:
  - Under the answer the UI shows the file name and the page or pages only: no markers in the answer text, no change to `answer.system`, no preview panel.
  - Pages are attributed after the answer is complete, deterministically: a retrieved chunk is cited when it shares at least `citations.min_shared_ngrams` normalized word n-grams (`citations.ngram` words long) with the answer (`config/docs.yaml`); its page joins the list. When no chunk passes, nothing is shown, so general-knowledge answers and "not found in the document" answers show no source.
  - Core change outside Part A: `Source` gains optional `page`, `document_id` and `excerpt`. `excerpt` is the sanitized chunk text the attribution compares against; it is never streamed and never part of a citation (assumed: the attribution needs the retrieved chunk's text, which the core sees only through the capability's output). The `citation` event and `done.citations` carry, per document, the attachment id, the file name and the pages: `Citation` gains `document_id` and `pages`, an additive change to `docs/events.schema.json` (id stays `events.v1`).
  - `validate` computes the citations and keeps its check that every citation names a source of this turn.
  - `rag.jsonl` measures citation page accuracy against these pages, and has cases where a docs conversation gets a general-knowledge question or one the document cannot answer, expecting no source line.
- Consequences: A citation says which pages the answer drew on, not which sentence came from where. A paraphrase that shares no n-gram with any chunk shows no source. Supersedes I12 and the preview panel of D2 as stated in the phase prompt.

### ADR-0141: rag.jsonl: 40 cases over four generated Arabic documents; Ragas scores through LiteLLM's judge alias
- Date: 2026-10-11
- Phase: 04 (implementation choice I13)
- Status: decided
- Context: README §12 names Ragas for RAG metrics; rule 6 sends every model call through Context Assembly and LiteLLM.
- Decision: `evals/golden/rag.jsonl` has at least 40 cases over at least four generated Arabic documents (a DOCX with headings, an XLSX, a multi-page PDF, a PDF with fake PII). Thresholds in `evals/thresholds.yaml`: retrieval hit@8 ≥ 0.90, citation page accuracy ≥ 0.85, Ragas faithfulness ≥ 0.85, answer correctness ≥ 0.80, correct abstention on not-in-document questions ≥ 0.90. Accepted interpretation of rule 6: Ragas scoring calls LiteLLM's alias for the `judge` role directly; the eval harness is a client of the platform, like a user's browser, not an agent model call, so it does not go through Context Assembly. The agent's own calls are unchanged.
- Consequences: Eval scores depend on the judge alias's model; a judge change is re-measured like any model change.

### ADR-0142: Agent and gateway API versions move to 1.4.0
- Date: 2026-10-11
- Phase: 04 (implementation choice I14)
- Status: decided
- Context: Phase 04 adds attachment endpoints and changes the turn request.
- Decision: One bump for the whole phase: agent and gateway OpenAPI 1.4.0. `docs/events.schema.json` changes additively (id stays `events.v1`).
- Consequences: The drift tests (ADR-0044) pin both documents at 1.4.0 until the PR merges.

### ADR-0143: A deterministic test service answers the embed and rerank aliases in `make test`
- Date: 2026-10-11
- Phase: 04 (implementation choice I15)
- Status: decided
- Context: `config.mock.yaml` returned one fixed vector for every text and had no rerank alias, so retrieval ranking could not be tested.
- Decision: A small test-only service (`embed-mock`, compose profile `test`) sits behind the mock LiteLLM config for the `embed` and `rerank` aliases: an embedding is a hashed bag of the words normalized by `app/text/arabic.py`; a rerank score is the word overlap with the query. Texts sharing more words rank higher, identically on every run. `make test` uses it; the real stack never does. Retrieval tests assert ranking (the expected chunk first or in the top 3). The fixed vector stays only where a test needs identical similarity (memory isolation), under the alias `embed-fixed`, named as such.
- Consequences: Memory tests that relied on every text matching every other choose the fixed alias explicitly.

### ADR-0144: Retrieval and evidence blocks are placeholders in captured model input
- Date: 2026-10-11
- Phase: 04 (owner decision on the G5 stop; core change outside Part A)
- Status: decided
- Context: With `OBSERVABILITY_CAPTURE_CONTENT` on, generation spans carry their input messages, which now include document chunks (I9).
- Decision: The `findings`, `retrieval` and `tool_results` blocks of a captured package become a placeholder naming the block, its token count and an HMAC of its text, the mechanism ADR-0081 uses for memory and summary.
- Consequences: Langfuse shows that evidence was present and how large it was, never its text. The answer itself is still captured (ADR-0021).

### ADR-0145: The `conversation_attachments` routing rule makes a capability a router-model candidate
- Date: 2026-10-11
- Phase: 04 (owner decision on the G5 stop; I11)
- Status: decided
- Context: Manifest rules were `toggled`, `keyword`, `attachments` and `fallback` (ADR-0015); none could make a capability eligible on a later turn without deciding for it.
- Decision: A generic rule `conversation_attachments`: when the conversation holds indexed attachments, the capability is a candidate. When no deterministic rule selected anything and there are candidates, the router model chooses among the candidates and the fallback capabilities; when there are none, the fallback applies as before. The core names no capability.
- Consequences: Adding `conversation_attachments` to any manifest makes it routable this way.

### ADR-0146: kb: and tool: exceptions at OUTPUT and DATA stay refused
- Date: 2026-10-11
- Phase: 04 (tech debt F5; ADR-0108)
- Status: decided
- Context: ADR-0108 refused `kb:` and `tool:` policies that weaken a wider policy at OUTPUT or DATA until phase 04 attributes each part of an answer to its source.
- Decision: The refusal stays. A generated answer cannot be attributed reliably to its sources: the page attribution of ADR-0140 says which pages an answer drew on, not which sentence came from which source, so an exception scoped to one KB or tool cannot be applied to the part of the answer that came from it. Revisit with the kb capability in phase 05.
- Consequences: Exceptions for a KB or a tool remain possible at INPUT, TOOL_LIST, PRE_TOOL and POST_TOOL only.

### ADR-0147: `make smoke-airgap`: an internal network for every container but litellm, and a fixed ingress relay
- Date: 2026-10-11
- Phase: 04 (tech debt F2)
- Status: assumed
- Context: F2 asks for smoke on a stack where only `litellm` can reach out. Measured on the development host (Docker Desktop): a container on an `internal: true` network has no route out, but its published ports do not answer either; a bridge network without IP masquerade still reaches the internet through Docker Desktop's own NAT. Smoke and a browser need the gateway, Keycloak, the web app and Langfuse on the host's ports.
- Decision: `docker-compose.airgap.yml` puts every service on `airgap` (internal) only; `litellm` also joins `egress` for the model endpoints. `edge`, an `nginx:1.27.5-alpine` stream relay with a fixed configuration (`infra/airgap/nginx.conf`: four TCP forwards to `web`, `gateway`, `keycloak`, `langfuse-web`), joins `airgap` and `ingress` and publishes the four host ports; it runs no application code. `scripts/airgap_check.sh` fails unless every container's networks are internal except `litellm:egress` and `edge:ingress`, a probe container on `airgap` cannot reach the internet, the agent cannot, and litellm still answers an embedding call. `make smoke-airgap` brings the overlay up, runs the check and `scripts/smoke.sh` with `COMPOSE_FILE` set (so smoke's own restart keeps the layout), and restores the normal stack, pass or fail. It replaces the owner's manual offline-profile run (HANDOFF).
- Consequences: Two containers can reach out instead of one: litellm by design and the relay by necessity. The relay forwards inbound connections only and names no outside destination; a compromise of the relay image is the residual risk. Switching to and from the overlay recreates every container (about two minutes).

### ADR-0148: The seed users `analyst` and `evaluator` hold `cap:docs`
- Date: 2026-10-11
- Phase: 04
- Status: assumed
- Context: E2 has the viewer probe the analyst's attachment, so the analyst must be able to upload; the rag and routing suites run as `evaluator` (ADR-0074). Neither held `cap:docs`; the gateway requires it to upload.
- Decision: `infra/keycloak/realm-export.json` gives both users `cap:docs`. README §11 gains a note. The development realm was re-imported by recreating only the Keycloak database (`DROP DATABASE keycloak`, then `make up`), which is what a fresh import does; the owner's `make clean && make up` gives the same realm.
- Consequences: The analyst can use documents; `cap:docs` is still not part of the base `user` role.

### ADR-0149: The router model reads the conversation's file names as user-role data
- Date: 2026-10-11
- Phase: 04 (I11)
- Status: assumed
- Context: The first routing run scored 31/34: three questions about the file's topic that did not say "the file" ("متى تبدأ ساعات العمل الصيفية؟") went to `general`, because the router saw only the message and the candidates.
- Decision: The router's request (`router.request@1.0.0`) carries the names of the conversation's indexed files inside `<conversation_files>` and the message inside `<user_message>`, in the user role (ADR-0068: file names are user data); `router.system@1.1.0` routes a question on a topic the files may cover to `docs`. The names are part of the router cache key.
- Consequences: The second run scored 34/34. A file name is visible to the fast tier's model, as the message is. Routing still never sees file content.

### ADR-0150: LiteLLM is published on the loopback interface for `make eval`
- Date: 2026-10-11
- Phase: 04 (I13)
- Status: assumed
- Context: Ragas scores call LiteLLM's judge alias from the host (ADR-0141); LiteLLM had no published port.
- Decision: `docker-compose.yml` publishes `litellm` on `127.0.0.1:${LITELLM_PORT:-4000}` only, behind its master key. The development overlay keeps its own mapping.
- Consequences: Any local process with the master key from `.env` can call the model aliases, as `make eval` does; nothing outside the host can.

### ADR-0151: Attachment API details
- Date: 2026-10-11
- Phase: 04 (A1, A2)
- Status: assumed
- Context: I1 and A1 fix the shape; the details below were open.
- Decision:
  - The agent owns the attachment record (`agent.attachments`, migration `0009`); the gateway stores bytes in MinIO under `{conversation_id}/{random uuid}` and registers them with `POST /v1/conversations/{id}/attachments` on the agent, which binds a new conversation to the caller like a first turn (ADR-0050) and enforces the file count under a transaction lock. The object key never reaches a browser.
  - Uploading needs `cap:docs` (403 `forbidden`); list, status, download and delete need ownership only, so a user can always see and remove what they uploaded. Another owner's conversation is 403 `conversation_forbidden`; an attachment id the caller does not have in that conversation, another user's included, is 404 `attachment_not_found`, at the gateway and at the agent. A conversation nobody has used lists nothing and binds nobody.
  - The turn request names `attachment_ids` (lower-case UUIDs, at most 10); `ingest_attachments`, after `guard_input`, loads every live attachment of the conversation into `AgentState.attachments` with its status and ends the turn with `attachment_not_found` when the turn names one the caller does not have. `turn_attachment_ids` (AgentState addition) feeds the `attachments` rule. Every turn moves the conversation's expiry (ADR-0125).
  - The gateway answers `GET /v1/attachments/limits` from `config/docs.yaml`, so the composer shows the limits before upload. A body over the multipart cap (21 MB) is 413 `file_too_large` before the controller runs and is not audited (ADR-0013); 20 MB to 21 MB is refused by the controller and audited.
  - MinIO is reached with the root credentials from `.env`, as Langfuse already is (ADR-0022); a scoped key is phase 09.
- Consequences: The file count is checked twice (gateway pre-check, agent under a lock). A turn that names a stranger's attachment id fails the turn rather than ignoring the id.

### ADR-0152: Retrieval details
- Date: 2026-10-11
- Phase: 04 (I5, I8, I9)
- Status: assumed
- Context: The ADRs of I5 and I8 leave the query, the full-text query form and failures open.
- Decision:
  - The query is the turn's message alone; no rewriting model call. Full text matches any of the message's search stems (an OR `tsquery`) less the stopwords of `config/docs.yaml`, ranked by `ts_rank_cd`.
  - The tool returns chunk texts in its `content`, each after a `⟦chunk {id}⟧` line, so `sanitize_untrusted` runs on them at POST_TOOL; the capability splits the sanitized text back by id. Chunk metadata travels in `sources`.
  - The `rag.retrieve` span records, per returned chunk, its id, attachment id, page, a SHA-256 prefix of its section path (never the heading), score and ranks; the rag suite matches expected sections by that hash.
  - When the search fails, the evidence says the files could not be searched, so the answer never presents general knowledge as the files' content; files still pending or failed are named in the evidence, and the capability never waits for them.
- Consequences: A follow-up such as "وماذا عن المادة الخامسة؟" retrieves on its own words only. The first end-to-end run, before this rule, answered a maternity question from general knowledge while the reranker alias was missing.

### ADR-0153: Tool handlers reach the retrieval library through a binding the services push and pop
- Date: 2026-10-11
- Phase: 04 (A3)
- Status: assumed
- Context: A `ToolSpec` handler receives only its arguments (README §9), and capabilities register tools before the services exist; the docs tool needs the retriever (database pool, embedding and rerank clients).
- Decision: `app.rag.runtime` holds the retrievers of the started services: `start_services` binds, `stop_services` unbinds, and `retriever()` returns the most recent. The owner comes from the tool's arguments, which the capability fills from its RuntimeContext principal (trusted first-party code, ADR-0054); every VectorStore query names it. No broker or contract change.
- Consequences: One process runs one service set, so the binding is unambiguous in production; tests that start several sets keep working because a stopped set leaves the stack.

### ADR-0154: How the rag and routing suites measure
- Date: 2026-10-11
- Phase: 04 (I11, I13)
- Status: assumed
- Context: I13 names the metrics and thresholds; the method was open.
- Decision:
  - Every case runs in a new conversation: its generated document is uploaded through the gateway and indexed first. Rag cases send the file with the question (the `attachments` rule); routing cases send their message without it, so the router decides, except two cases marked `attach`.
  - hit@8: an expected page, or the hash of an expected section, among the chunks the turn's `rag.retrieve` span reports (answerable and PII cases). Citation page accuracy: answerable PDF cases whose cited pages are non-empty and within the expected pages, plus general-knowledge and not-in-document cases that cite nothing. Abstention: the answer says the document lacks the answer (a list of Arabic and English phrases); that it shows no source is scored once, under citation page accuracy. Faithfulness and answer correctness: Ragas 0.2.15 on answerable cases, with the judge alias; the contexts are the text of the retrieved pages and sections from `evals/docs/sections.json`, each under the source label the answer model saw ("file، صفحة 3"), and the reference is the case's reference answer naming its source, because `answer.system` asks every answer to name the source of each fact and the judge otherwise counts that naming as an unsupported claim (first run: one-sentence correct answers scored 0.50).
  - General-knowledge cases of the rag suite send their message without the file, so the router decides, as on a later turn of a document conversation; sent with the file, the turn is a docs turn by the `attachments` rule.
  - `make eval s=<suite>` runs one suite; `make eval` runs them all.
- Consequences: A run of the rag suite uploads 47 files and makes 47 turns and about 35 Ragas scorings; it takes about 20 minutes on the OpenRouter profile.

### ADR-0155: Web choices for documents
- Date: 2026-10-11
- Phase: 04 (D1, D2)
- Status: assumed
- Context: D1 and D2 of the phase prompt leave the layout open.
- Decision: A paperclip button and drag-and-drop on the composer; each file is a chip with its name in `<bdi dir="auto">`, an upload percentage, then a badge (قيد المعالجة، جاهز، تعذّرت القراءة) and, when failed, the Arabic reason. The limits line comes from `GET /v1/attachments/limits`. Sending is disabled, with a short Arabic note, while a file uploads or is pending; removing a chip deletes the attachment. The source line under an answer reads "المصدر", then each file name and "الصفحة 3" / "الصفحتان 2 و5" / "الصفحات …" (three keys, no ICU plural), or the section of a document without pages. Numbers use the locale's default digits, as the rest of the UI. The vitest include gains `.test.tsx`.
- Consequences: The turn's own file names show under the user's message; the conversation's earlier files are not listed in the UI yet.

### ADR-0156: Page attribution counts word bigrams, with a floor relative to the best chunk
- Date: 2026-10-11
- Phase: 04 (ADR-0140)
- Status: assumed
- Context: ADR-0140 leaves the n-gram size and threshold to `config/docs.yaml`. The first rag run used trigrams with at least two shared: a paraphrased answer ("الحد الأقصى للإجازة الاضطرارية ... خمسة أيام، وذلك بموافقة المدير") shared no trigram with its page and showed no source, while the annual-leave answer also cited the sick-leave page, which shares only "ثلاثون يوما براتب كامل". Measured offline on that run's 29 answers with a citation expectation: trigrams, two shared: 22/29; bigrams, three shared: 23/29; bigrams, three shared and at least half of the best chunk's count: 25/29, with the remaining misses general-knowledge answers given on a docs turn and two abstentions that name the document.
- Decision: `citations: {ngram: 2, min_shared_ngrams: 3, relative_to_best: 0.5}`: a chunk is cited when it shares at least three word bigrams with the answer and at least half as many as the best-matching chunk. Still deterministic, no model call, no change to the answer.
- Consequences: An answer that names the document's title can still cite the page that holds the title. A page that repeats a common phrase beside the page the answer used is no longer cited.

