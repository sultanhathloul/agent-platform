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
- Status: decided — Superseded by ADR-0166 on 2026-10-12
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
- Status: decided — Superseded by ADR-0173 on 2026-10-12 (visual-order detection), ADR-0174 (chunk budget, XLSX header)
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
- Status: decided — Superseded by ADR-0163 on 2026-10-12 (where attribution runs, its input and validate's check)
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
- Status: decided — Superseded by ADR-0167 on 2026-10-12
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
- Status: assumed — Accepted on 2026-10-12
- Context: The first routing run scored 31/34: three questions about the file's topic that did not say "the file" ("متى تبدأ ساعات العمل الصيفية؟") went to `general`, because the router saw only the message and the candidates.
- Decision: The router's request (`router.request@1.0.0`) carries the names of the conversation's indexed files inside `<conversation_files>` and the message inside `<user_message>`, in the user role (ADR-0068: file names are user data); `router.system@1.1.0` routes a question on a topic the files may cover to `docs`. The names are part of the router cache key.
- Consequences: The second run scored 34/34. A file name is visible to the fast tier's model, as the message is. Routing still never sees file content.

### ADR-0150: LiteLLM is published on the loopback interface for `make eval`
- Date: 2026-10-11
- Phase: 04 (I13)
- Status: assumed — Superseded by ADR-0172 on 2026-10-12 (the development overlay's mapping)
- Context: Ragas scores call LiteLLM's judge alias from the host (ADR-0141); LiteLLM had no published port.
- Decision: `docker-compose.yml` publishes `litellm` on `127.0.0.1:${LITELLM_PORT:-4000}` only, behind its master key. The development overlay keeps its own mapping.
- Consequences: Any local process with the master key from `.env` can call the model aliases, as `make eval` does; nothing outside the host can.

### ADR-0151: Attachment API details
- Date: 2026-10-11
- Phase: 04 (A1, A2)
- Status: assumed — Superseded by ADR-0166 on 2026-10-12 (delete and expiry only)
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
- Status: assumed — Superseded by ADR-0159 on 2026-10-12
- Context: A `ToolSpec` handler receives only its arguments (README §9), and capabilities register tools before the services exist; the docs tool needs the retriever (database pool, embedding and rerank clients).
- Decision: `app.rag.runtime` holds the retrievers of the started services: `start_services` binds, `stop_services` unbinds, and `retriever()` returns the most recent. The owner comes from the tool's arguments, which the capability fills from its RuntimeContext principal (trusted first-party code, ADR-0054); every VectorStore query names it. No broker or contract change.
- Consequences: One process runs one service set, so the binding is unambiguous in production; tests that start several sets keep working because a stopped set leaves the stack.

### ADR-0154: How the rag and routing suites measure
- Date: 2026-10-11
- Phase: 04 (I11, I13)
- Status: assumed — Superseded by ADR-0181 on 2026-10-12 (abstention, number matching, the Ragas reference)
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
- Status: assumed — Superseded by ADR-0166 on 2026-10-12 (removing a chip no longer deletes)
- Context: D1 and D2 of the phase prompt leave the layout open.
- Decision: A paperclip button and drag-and-drop on the composer; each file is a chip with its name in `<bdi dir="auto">`, an upload percentage, then a badge (قيد المعالجة، جاهز، تعذّرت القراءة) and, when failed, the Arabic reason. The limits line comes from `GET /v1/attachments/limits`. Sending is disabled, with a short Arabic note, while a file uploads or is pending; removing a chip deletes the attachment. The source line under an answer reads "المصدر", then each file name and "الصفحة 3" / "الصفحتان 2 و5" / "الصفحات …" (three keys, no ICU plural), or the section of a document without pages. Numbers use the locale's default digits, as the rest of the UI. The vitest include gains `.test.tsx`.
- Consequences: The turn's own file names show under the user's message; the conversation's earlier files are not listed in the UI yet.

### ADR-0156: Page attribution counts word bigrams, with a floor relative to the best chunk
- Date: 2026-10-11
- Phase: 04 (ADR-0140)
- Status: assumed — Superseded by ADR-0182 on 2026-10-12 (the attribution rule)
- Context: ADR-0140 leaves the n-gram size and threshold to `config/docs.yaml`. The first rag run used trigrams with at least two shared: a paraphrased answer ("الحد الأقصى للإجازة الاضطرارية ... خمسة أيام، وذلك بموافقة المدير") shared no trigram with its page and showed no source, while the annual-leave answer also cited the sick-leave page, which shares only "ثلاثون يوما براتب كامل". Measured offline on that run's 29 answers with a citation expectation: trigrams, two shared: 22/29; bigrams, three shared: 23/29; bigrams, three shared and at least half of the best chunk's count: 25/29, with the remaining misses general-knowledge answers given on a docs turn and two abstentions that name the document.
- Decision: `citations: {ngram: 2, min_shared_ngrams: 3, relative_to_best: 0.5}`: a chunk is cited when it shares at least three word bigrams with the answer and at least half as many as the best-matching chunk. Still deterministic, no model call, no change to the answer.
- Consequences: An answer that names the document's title can still cite the page that holds the title. A page that repeats a common phrase beside the page the answer used is no longer cited.


### ADR-0157: The reranker scores the best 16 fused candidates on their first 400 tokens
- Date: 2026-10-11
- Phase: 04 (ADR-0133, ADR-0134)
- Status: assumed — Superseded by ADR-0184 on 2026-10-12 (rerank off by default; 16 candidates on 1070 characters when on)
- Context: Measured on the development host (CPU inference, 24 threads), the cross-encoder's time grows with the tokens it scores: 30 candidates of about 800 tokens took 34 s, 16 took 18 s, 8 took 8 s, 30 of about 270 tokens took 10 s. A docs turn on a 20-page Arabic PDF spent 27.3 s (p50, n=20) in `rag.retrieve` and reached its first token after 28.7 s.
- Decision: `retrieval.rerank_candidates: 16` and `retrieval.rerank_max_tokens: 400` in `config/docs.yaml`: the best 16 of the fused 60 are reranked, each on its first 400 tokens (counted as Context Assembly counts), and the top 8 are kept with their full text. The vector and text searches (30 + 30, RRF) are unchanged.
- Consequences: `rag.retrieve` p50 6.31 s and time to first token p50 7.72 s on the same document (n=20). A chunk whose answer lies past its first 400 tokens is ranked by its beginning; it still reaches the top 8 when fusion ranks it high. On a GPU-served reranker in production both values can go back up; they are configuration.

### ADR-0158: No policy at ingestion; delimiters are neutralized at runtime, for everyone
- Date: 2026-10-12
- Phase: 04 (owner principle, review round 1)
- Status: decided
- Context: Review round 1 found PII and instruction-like text handled in several places, and asked where a policy may act on uploaded content.
- Decision: No guard policy is applied when a file is ingested: chunks, headings, sheet titles and file names are stored as extracted. Every guard policy runs at runtime, per principal (POST_TOOL on what a backend returns, OUTPUT and DATA on what leaves), so role exceptions (ADR-0107) keep working for their holders. Neutralizing delimiter tokens is not a policy: it applies to everyone, also at runtime, wherever untrusted text reaches a prompt.
- Consequences: A document backend applies no policy (the contract suite asserts it, ADR-0159). Redacting a national ID in a section heading happens on the way out, for the user who asks, and not for a holder of an exception.

### ADR-0159: The docs capability talks to one DocumentBackend; the phase 04 pipeline is InternalDocumentBackend
- Date: 2026-10-12
- Phase: 04 (owner decision C6; supersedes ADR-0153)
- Status: decided
- Context: The owner plans a RAG/OCR service of their own. The docs capability reached the vector store and the retriever through a module-global binding (ADR-0153), and its tool took the owner from its arguments.
- Decision:
  - `app/rag/backend.py` defines `DocumentBackend`: `register(stored file)`, `status(document id, scope)` (pending, indexed, failed with a reason), `search(query, scope, k)` (sources: chunk reference, text, page, section, file name, score) and `fetch(chunk references, scope)` (texts). `DocumentScope` names the tenant, the user and the conversation; every backend enforces it and returns nothing outside it, whatever reference a call names. A backend applies no policy (ADR-0158).
  - `InternalDocumentBackend` (`app/rag/internal.py`) is this phase's pipeline: registering enqueues the ingestion job, status is the attachment record's, search is the hybrid retrieval of ADR-0133 over the conversation's indexed files, fetch reads chunk texts from the VectorStore. `config/docs.yaml` `backend: internal` selects it; a future MCP backend implements the same protocol and is named there. Not built now.
  - The capability imports no VectorStore, extractor, chunker or retriever. Its tool receives a `ToolContext` from the broker: the scope (built from the verified principal and the conversation in state, never from arguments, ADR-0160) and the backend the capability's `RuntimeContext` carries. `app/rag/runtime.py` and its binding are removed.
  - The core reads an attachment's status through the backend: the attachment record says what was uploaded, the backend how far indexing went (the turn's attachments, the status endpoint).
  - A contract suite (`tests/integration/test_document_backend_contract.py`) runs against the internal backend and a test-only in-memory fake (`tests/fixtures/fake_backend.py`): scope isolation across users, tenants and conversations, page and section metadata, status transitions, and untrusted text returned unchanged as data. The docs capability's turn tests run on both backends with no change to capability code.
- Consequences: Whatever a backend returns passes the same runtime POST_TOOL, OUTPUT and DATA path. A backend's own spans (the internal one's `rag.retrieve`) are its own; the capability's span and the broker's are the same for every backend.

### ADR-0160: A tool's scope comes from the principal; a capability calls only the tools its manifest declares
- Date: 2026-10-12
- Phase: 04 (owner decision S1; supersedes the owner-from-arguments part of ADR-0153)
- Status: decided
- Context: `docs_search` took `tenant_id`, `user_id` and `collection` from its arguments, which the capability filled from its principal; the broker never checked them, and any capability could call any registered tool.
- Decision:
  - `docs_search` takes the query (and `k`) only; its schema forbids any other field, so a call naming another owner or a collection fails validation before anything runs. The broker builds a `ToolScope` from the verified principal and the turn's conversation id in state and passes it to the handler in its `ToolContext`; the backend enforces it (ADR-0159).
  - The manifest gains `tools`: the tools a capability may call. At startup every declared tool must be registered by some capability. The broker refuses a call to a tool the caller's manifest does not declare, registered or not: nothing runs and nothing streams, the call returns `denied` / `undeclared_tool`, and an audit-only guard record (PRE_TOOL, `block`, reason `undeclared_tool`, naming the caller and the tool) reaches the turn's audit delta. Calls through the broker name their caller.
- Consequences: Adding a tool to a capability is a manifest line as well as a `tools.py` entry. A capability that wants another capability's tool must declare it, which a reviewer sees.

### ADR-0161: The manifest's context.needs drives the answer package; config/context.yaml supplies defaults only
- Date: 2026-10-12
- Phase: 04 (owner decision C2)
- Status: decided
- Context: README §18.4 says Context Assembly builds a capability's package from `context.needs`, but the answer package of every turn used `nodes.answer.needs` of config/context.yaml whatever ran, so a general turn in a conversation with files had the same blocks as a docs turn, and `needs: []` meant nothing for the answer.
- Decision: The answer package's optional blocks are the union of the turn's capabilities' `context.needs`, in pipeline order; the system instructions and the user message are always there. A manifest that leaves `needs` out takes `nodes.answer.needs`; `needs: []` yields no optional block. `general` declares `[state_digest, summary, window, memory]`, `docs` adds its evidence. A capability's own model calls use its `needs` as before (none when it leaves them out).
- Consequences: A capability that wants history in the answer says so in its manifest. A general turn in a conversation with indexed files carries no evidence block (tested), and adding an evidence-producing capability adds its blocks only to its own turns.

### ADR-0162: Chunk text never enters AgentState; the answer step fetches it through the DocumentBackend
- Date: 2026-10-12
- Phase: 04 (owner decision S4, the state part)
- Status: decided
- Context: The docs capability's findings held every retrieved chunk inside its evidence, and each Source carried its chunk as `excerpt`; both were checkpointed with the turn, so document text lived in every checkpoint of the conversation.
- Decision:
  - A capability returns source references: `Source` loses `excerpt` and gains `ref` (the chunk's DocumentBackend reference) and `score`. `docs_search` returns references with file name, page, section and score, and no text; the docs findings are notes only (passages may not answer; pending and failed files; a failed search).
  - The answer step, when its blocks include `retrieval` (ADR-0161), fetches the referenced chunks through `DocumentBackend.fetch`, scoped to the verified principal and the conversation, and checks the texts at POST_TOOL (sanitize_untrusted first, then the policies) under the scope of the capability that found them, as any tool output. Context Assembly renders each chunk in its own `context.retrieval` block (2.0.0: the file and page or section in the delimiter only, escaped) and compresses a chunk's text, never its delimiters. A POST_TOOL block withholds that capability's evidence.
  - The texts stay on the turn context for page attribution (ADR-0163) and are gone when the turn ends.
- Consequences: A checkpoint holds the answer and the references, not the passages (tested). The evidence of an answer is fetched once per answer call (twice on a rewrite). The evidence budget is Context Assembly's `blocks.retrieval`; `retrieval.evidence_max_tokens` leaves config/docs.yaml.

### ADR-0163: Capabilities may supply citations; page attribution is a separate generic step before validate, without the question's words
- Date: 2026-10-12
- Phase: 04 (owner decision C1; supersedes ADR-0140 on where attribution runs and what validate checks)
- Status: decided — Superseded by ADR-0182 on 2026-10-12 (the attribution rule only; the separate step, validate and the prompts stand)
- Context: validate computed the citations itself, so its "citations ⊆ sources" check could never fail; attribution read docs-named configuration; "not found" answers restating the question cited a page (the known gap of the first run); answers carried "[docs]" labels copied from the findings block, and `answer.system` asked every answer to name its sources.
- Decision:
  - `CapabilityOutput` gains `citations`. A new node `attribute_citations`, after `answer` and before `validate`, keeps the capabilities' citations and adds page attribution's, capability-agnostically, from sources with a document and page or section metadata, comparing the answer with the chunk texts the answer step fetched. `validate` checks every citation against this turn's sources and fails with `citation_not_in_sources` otherwise (tested); attribution never bypasses it.
  - Attribution drops the question's words from the answer before taking its n-grams (owner decision). The rule moves to the generic `config/citations.yaml` (`attribution: ngram 2, min_shared_ngrams 3, relative_to_best 0.5`, values unchanged from ADR-0156).
  - `context.findings` 1.1.0 no longer labels each finding with its capability, and says not to copy tags or bracketed file names; `answer.system` (ar 1.4.0, en 1.2.0) no longer asks the answer to name its sources and forbids bracketed tags and file names. Unit tests reject a label in either prompt.
- Consequences: An abstention that only restates the question shows no source. An answer quoting a heading or a phrase the question did not contain still cites its page. Answers no longer name their sources in text; the source line under the answer does.

### ADR-0164: The core holds no docs-named configuration or type; a capability reads its package and its configuration through its runtime context
- Date: 2026-10-12
- Phase: 04 (owner decision C3)
- Status: decided
- Context: `Services` held `docs_config` and the retriever; `validate` read `docs_config.citations`; the docs capability loaded config/docs.yaml through the core's settings; every capability got a context package assembled whether it read it or not.
- Decision:
  - `Services` holds the generic `DocumentBackend` and `citations` (config/citations.yaml, ADR-0163); `docs_config` and the retriever are gone. `app/rag/setup.py` builds the backend `config/docs.yaml` names (and, for the internal backend, its pipeline and worker); the core calls it. The attachment service takes a generic `UploadLimits` (the `limits` of config/docs.yaml, the file the gateway reads too).
  - `RuntimeContext` gains `context()` and `config`. `context()` assembles the capability's package per its `context.needs` on the first call only and returns a fresh detached copy on each; `CapabilityInput.context` is removed, so no package is assembled, resolved or recorded for a capability that never reads it (tested). `config` is a copy of `config/<slug>.yaml` when that file exists; the docs capability reads its `retrieval.top_k` there and passes it to its tool.
- Consequences: A turn of `general` or `docs` assembles and records one package fewer. A capability that needs its package awaits `rt.context()`; README §18.3's `CapabilityInput.context` is amended by this entry.

### ADR-0165: Untrusted content stays data: user role, decode before strip, escaped attributes, every readable field checked
- Date: 2026-10-12
- Phase: 04 (owner decision S2)
- Status: decided
- Context: Review round 1 found two ways out of the untrusted block: a section heading reached the evidence's `source` attribute unsanitized, and `sanitize_untrusted` decoded entities after stripping tags, so `&lt;/untrusted&gt;` became a live closing tag; the evidence then sat in the system role. File names reached prompts unescaped, and a citation's section reached the client without OUTPUT or DATA.
- Decision:
  - The `findings`, `retrieval` and `tool_results` blocks are in the user role and merge with the question into the current user message (ADR-0076, ADR-0068); no untrusted text reaches the system message.
  - `sanitize_untrusted` decodes first (HTML entities until stable, at most three rounds; full-width bracket look-alikes folded), then strips scripts, comments and tags, then neutralizes any angle bracket left (`‹ ›`), so no delimiter can be formed. It runs on a tool's content and on every readable source field (`title`, `locator`: file names, section headings, sheet titles). Neutralization is not a policy and applies to everyone (ADR-0158).
  - Delimiter attribute values are escaped in every evidence template (`context.retrieval` 2.0.0, `context.tool_result` 1.1.0, `docs.evidence` 2.0.0, `router.request` 1.1.0); the names of pending and failed files render inside a delimited block.
  - POST_TOOL, INPUT and the citation checks evaluate several texts as one: the texts are joined with marker lines whose tag is derived from the texts (no digits, so no PII pattern matches it; a document cannot forge it), checked once, and split back; a field whose marker did not survive comes back empty. POST_TOOL checks a tool's content with its sources' titles and locators; INPUT checks the message with the names of the files sent with the turn, and the checked names replace the names in state, so the router and the answer see only checked names. Attachments are therefore loaded before INPUT.
  - Citation titles and locators pass OUTPUT and DATA per principal, like the answer, after its own checks; citations leave from `release_answer`, with their checked values, in both answer modes. A block on a citation text ends the turn as the answer's would; a rewrite cannot apply to a file name or a heading, so it withholds the citations.
  - Compression never touches a delimiter line: retrieval chunks are compressed inside their block and re-rendered, and findings are compressed between delimiter lines (`extractive_delimited`).
- Consequences: Tests: an `.md` heading `x"></untrusted> …`, an entity-encoded and a full-width closing tag, a file name with `< > "` (indexed and pending), an INPUT redaction and block on a file name, a national ID in a DOCX heading redacted in the citation for a user and shown to the holder of a role exception, and two capabilities' findings compressed with both blocks whole, on both document backends. A turn with citations runs OUTPUT and DATA once more each, on a few short texts.

### ADR-0166: The product deletes no uploaded file; operations can delete cleanly
- Date: 2026-10-12
- Phase: 04 (owner decision S4, supersedes D1 / ADR-0125, and the delete and expiry parts of ADR-0151 and ADR-0155)
- Status: decided
- Context: Review round 1 found the user delete and the daily purge incomplete (chunk text stayed in checkpoints and context packages; an object a storage outage left was orphaned; the purge handled 500 records a day). The owner decided against deletion in the product.
- Decision:
  - No deletion in the product: the user delete endpoint (agent and gateway), the web's delete on removing a chip, `expires_at` and the touch on each turn, the daily purge job and `retention_days` / `purge` in config/docs.yaml are removed. Files stay for as long as the platform keeps them. The composer may drop only a refused or unreadable file, from the composer.
  - So that operations can delete cleanly: migration 0010 gives `agent.rag_chunks.attachment_id` a foreign key to `agent.attachments` with ON DELETE CASCADE (orphan chunks are deleted first), and `agent.context_packages` gains `attachment_ids`, the attachments whose chunks a package's retrieval blocks hold. Chunk text never enters AgentState (ADR-0162), so a checkpoint holds no passage. PROGRESS lists the manual steps.
  - An orphan sweeper removes objects of the uploads bucket that no attachment record names, once older than `orphan_sweeper.min_age_minutes` (60), every `interval_minutes` (30): uploads whose registration failed. It never touches an object a record names.
- Consequences: Storage grows with use; deleting a user's file is a request to operations. Phase 05's KB documents will need their own record for their chunks, since the key names the attachments table. Tests: the cascade, a chunk refused without its record, packages naming their attachments, the sweeper's age and record rules, and no delete at the agent (405) or the gateway (405).

### ADR-0167: Agent and gateway API versions move to 2.0.0
- Date: 2026-10-12
- Phase: 04 (owner decision C4; supersedes ADR-0142)
- Status: decided
- Context: Phase 04 replaced the turn request's `attachments` (objects) with `attachment_ids` (strings) in both APIs, and removed the attachment delete and `expires_at` (ADR-0166): a client of 1.3.0 breaks, so a minor version was wrong. The gateway document also gave the items of `attachment_ids` and `selected_kbs` a string default `"[]"`.
- Decision: Agent and gateway OpenAPI move to 2.0.0. The array default moves to the array (`@ArraySchema(arraySchema = @Schema(defaultValue = "[]"))`), so no item carries a default; `selected_kbs` had the same defect since phase 01 and is fixed with it. Changes until this PR merges stay within 2.0.0 (ADR-0046).
- Consequences: The drift tests pin both documents at 2.0.0; the web's generated types are regenerated.

### ADR-0168: File names reach the router only after INPUT, on every turn
- Date: 2026-10-12
- Phase: 04 (owner decision C5, with ADR-0149 accepted by the owner)
- Status: decided
- Context: The owner accepted ADR-0149 (the router reads the conversation's file names as user-role data) on the condition that the names reach the router only after the INPUT check. INPUT checks the files sent with a turn (ADR-0165), but an earlier file's name is read again from its record on every later turn, and a redaction INPUT made on its own turn is not stored.
- Decision: INPUT checks the message with the names of all the conversation's files, those sent with this turn and the earlier ones, since every one of them can reach a prompt this turn (the router reads the indexed files' names, the answer the pending and failed ones). The checked names replace the names in state before the router or a capability runs. `router.request` 1.1.0 escapes them (ADR-0165).
- Consequences: A policy added after a file was uploaded applies to its name from the next turn on (tested). A name an INPUT policy blocks blocks every later turn of that conversation, as it would block the turn that sent it.

### ADR-0169: The router's cache key names the user and the router alias
- Date: 2026-10-12
- Phase: 04 (owner decision S8; amends ADR-0139 and ADR-0149 on the cache key)
- Status: decided
- Context: The router decision was cached per tenant by the prompts, locale, candidates, file names, message and window. Two users of a tenant with the same file names and message shared a decision, and a change of the router's model or a tier ceiling reused answers the old model gave.
- Decision: The key adds the user id and the LiteLLM alias the `router` role resolves to for the principal (ceilings included), the judge cache's precedent (ADR-0110).
- Consequences: The cache hit rate falls to one user's repeated questions. A model change takes effect on the next turn (tested: another tenant, another user and another alias each miss the cache).

### ADR-0170: Ragas' usage tracking is off; the eval harness calls nothing outside the platform
- Date: 2026-10-12
- Phase: 04 (owner decision S3)
- Status: decided
- Context: Ragas 0.2.15 posts usage events to https://t.explodinggradients.com unless `RAGAS_DO_NOT_TRACK` is `true`, and it caches the value the first time it reads it. The harness never set it, so every `make eval` reached an outside service (CLAUDE.md: no external SaaS).
- Decision: `evals/run_evals.py` sets `RAGAS_DO_NOT_TRACK=true` at import, before anything imports Ragas (it is imported lazily, in the judge), and the Makefile's `eval` target sets it too. A test target, `make test-evals` (part of `make test`), proves it: the variable is set before Ragas is imported, a tracked event sends nothing, and the judge's model and embeddings point at the stack's LiteLLM only. The harness's other calls are the stack's own gateway, Keycloak and Langfuse.
- Consequences: Rule 11 reports a fifth test layer, `evals` (the harness's tests).

### ADR-0171: A judge call at POST_TOOL is content-free; extraction errors carry their type and reason code only
- Date: 2026-10-12
- Phase: 04 (owner decision S5)
- Status: decided
- Context: An llm_judge policy at POST_TOOL sent document chunks and their metadata to the judge, and the call's messages and output went to Langfuse with a recorded context package. The ingestion worker logged an unexpected failure with `log.exception`, and its span recorded the exception event: both carry the library's message, which can quote the document.
- Decision: `CheckContext` names the checkpoint; a judge call at POST_TOOL is content-free like one on a memory candidate or a summary (ADR-0110): no messages or output in its trace, hashes only, no recorded package. The `rag.ingest` span records no exception event; it carries `ingest.error_type` (the type's name) and `ingest.reason` (the code); the worker logs the type and `ingest_error`, never the message.
- Consequences: Debugging a parser failure uses the type, the code and the owner's own file, not the log.

### ADR-0172: LiteLLM and its test double listen on the loopback interface only; the test embedding service sits on a network of its own
- Date: 2026-10-12
- Phase: 04 (owner decision S6; supersedes ADR-0150's "the development overlay keeps its own mapping")
- Status: decided
- Context: The development overlay published the real LiteLLM on `0.0.0.0:4000` (`!override`), so anyone who could reach the host could use the model keys with the master key; `litellm-mock` was published on `0.0.0.0:4001`; `embed-mock` shared the default network with every service.
- Decision: The development overlay no longer maps LiteLLM: it keeps the base file's `127.0.0.1:${LITELLM_PORT}`. `litellm-mock` publishes `127.0.0.1:4001`. `embed-mock` joins only `test`, an internal network it shares with `litellm-mock`, its one client. A smoke check fails when LiteLLM or its double is published on any other address.
- Consequences: A tool on another machine cannot reach LiteLLM through the development stack; local pytest runs still reach the mock on 127.0.0.1:4001.

### ADR-0173: Arabic PDF lines: pypdf's logical order is trusted; runs are reordered only on presentation forms
- Date: 2026-10-12
- Phase: 04 (owner decisions A1 and A2; supersedes the visual-order part of ADR-0135)
- Status: decided
- Context: Review round 1 found every eval PDF line with digits or Latin scrambled («2031 الإصدار الثالث لعام», the ID after its label's colon), because pypdf already returns each right-to-left run's letters in logical order and `app/rag/bidi.py` reversed lines again on word heuristics, one of which («ends in لا») also reversed «قليلا جدا». The tests built their visual input as the inverse of the code under test.
- Decision:
  - pypdf's letter order is trusted. The one strong signal acted on is presentation forms together with a digit or Latin run in the same line: the producer shaped and laid the line out itself, and pypdf left its runs in visual order. The line's pieces (each left-to-right run, and the text between two of them, which pypdf turned already) are then put back in logical order, no piece's own text touched. The «لا» and reversed-particle heuristics are removed; no line is reversed on a guess from its words.
  - Every line then gets its Arabic/digit and Arabic/Latin boundaries spaced (LibreOffice-style PDFs come back «عام2027  إلى»), spaces collapsed, no space before a mark that ends a clause, and a stranded leading colon put back between the label and its value (any other leading mark at the line's end); NFKC folds presentation forms.
  - Fixtures (`services/agent/tests/fixtures/pdf/`, built by `make_fixtures.py`): a PDF LibreOffice exported from an Arabic DOCX (digits, Latin, a table, a label before a national ID and an IBAN, «قليلا جدا», «فعلا قليلا مثلا») and a visual-order PDF laid out by python-bidi (an implementation of the bidirectional algorithm this repository does not contain; a dev dependency). The test helper `visual_line` uses python-bidi too, so generated PDFs are laid out as real producers lay them out. Tests compare whole lines in order. DOCX, XLSX, TXT and MD each get a presentation-form test.
- Consequences: Every line of the eval PDFs (leave-regulation, cv-fake-pii, twenty-pages) comes back exactly as written; the regenerated eval PDFs are byte-identical, since the old helper agreed with python-bidi on their lines. A PDF that draws presentation forms in logical order would be misread; none is known among the producers tested.

### ADR-0174: No chunk passes its budget; an XLSX header is the first row followed by a row of its width
- Date: 2026-10-12
- Phase: 04 (owner decision A3; supersedes the chunking and header details of ADR-0135)
- Status: decided
- Context: After a cut, the chunker carried the overlap into the next chunk and added the next unit without checking the budget (a 207-token chunk at 200); a header longer than the budget left one token for rows; an XLSX title row above the table became the header repeated in every chunk.
- Decision: After a cut, the carried overlap gives way, oldest unit first, until the next unit fits. A repeated header is truncated to half the budget. An XLSX sheet's header is its first row (two cells or more) followed by a row of the same width; rows above it stay in the sheet's text; a sheet without such a pair takes its first row. Unit tests cover the overlap, the header, title rows, reciprocal rank fusion and the full-text stopwords on Arabic questions.
- Consequences: Overlap is shorter where a long unit follows a cut. A one-column table is read without a header.

### ADR-0175: A rerank switch; a malformed rerank answer is a span error and the fusion order stands
- Date: 2026-10-12
- Phase: 04 (owner decision R2)
- Status: decided
- Context: A rerank answer without results, or with a missing index, was read as zeros for the missing documents, which then ranked last on a score they never had; there was no way to run retrieval without the reranker.
- Decision: `config/docs.yaml` `retrieval.rerank` holds `enabled`, `candidates` and `max_chars` (how much of each candidate the reranker reads, in characters; null for the whole chunk). Switched off, the top `top_k` are taken in fusion order. A rerank answer that does not score every document exactly once with a finite number raises `RerankMalformed`; `rag.retrieve` then carries an error status and `rag.rerank.error`, and the fusion order stands. An unreachable reranker is still a failed search (ADR-0152). Unit tests cover each malformed shape, the switch, the fallback and the character bound.
- Consequences: Q1 measures `enabled` and `candidates`/`max_chars` against the rag thresholds (ADR-0157 is superseded there). Rerank cannot zero a chunk silently.

### ADR-0176: Hostile files: ingestion in its own service, each file parsed in a limited child process, archives and XML checked first
- Date: 2026-10-12
- Phase: 04 (owner decision R4, "no gaps"; supersedes ADR-0130's "worker tasks in the agent process")
- Status: decided
- Context: Parsing ran in the agent's process, on its event loop's threads, with no bound on archive inflation, XML entities, PDF streams, extracted text or chunks: one file could take a chat turn's CPU and memory, or the process.
- Decision:
  - Ingestion moves out of the agent into `ingest`, a container of the agent's image (`python -m app.rag.ingest.service`) with `mem_limit: 2g`: it takes jobs from the Redis queue the agent fills, re-queues pending attachments at start, runs the orphan sweeper (ADR-0166) and answers a health port. The agent never parses a file.
  - Each file is extracted and chunked in a child process (spawn) with `RLIMIT_CPU`, `RLIMIT_AS` and a wall-clock timeout (`ingest.sandbox`: 60 s, 1024 MB, 120 s). A child stopped by a limit, or one that dies, ends the file `failed` with `extraction_aborted`; the service goes on. The child sends data only: chunks, a page count, a reason code, or a defect's type name. Chunking runs there, off any event loop.
  - Before a DOCX or XLSX is parsed, its archive is checked (`archive`: 1000 entries, 100 MB uncompressed, ratio 200 for entries over 1 MB, inflated sizes counted as streamed); a nested archive is refused (`archive_unsafe`), and an XML part declaring a DTD or an entity is refused (`xml_unsafe`). Behind it: `defusedxml` is installed and openpyxl must report it in use at import (the extractor fails otherwise); python-docx parses with entity resolution, DTD loading and network access off.
  - PDF: the page limit as before; every pypdf decompression bound set to `extraction.max_pdf_stream_bytes` (50 MB) and each page's content decoded before its text is taken (pypdf's `extract_text` swallows the limit), `stream_too_large`; more than `extraction.max_chars` (1 000 000) of text, `text_too_long`, as soon as the page that passes it is read. Every kind: the same text cap and `chunking.max_chunks` (2000), `too_many_chunks`. An object over the upload limit is `file_too_large`.
  - Seven new reason codes, each with an Arabic and an English message in the web.
- Consequences: Tests: a zip bomb, an entity-expansion DOCX, an XXE DOCX, an 80 MB-stream PDF and a 50 000-row XLSX each end failed with their reason while a chat turn completes; a child out of time or memory ends as `extraction_aborted`; the parser walls are tested directly. Spawning a child costs about half a second per file. A DOCX with an embedded spreadsheet is refused as a nested archive.

### ADR-0177: One source for upload limits; cap:docs, size and rate checked before the body; the JSON 413, never a reset
- Date: 2026-10-12
- Phase: 04 (owner decision R3; supersedes ADR-0151's "list, status and download need ownership only" and its 21 MB multipart cap)
- Status: decided
- Context: The gateway's multipart caps were written in application.yml (21 MB / 22 MB) beside config/docs.yaml; only an upload checked `cap:docs`, after its body was stored to a temporary file; a body far over the cap made Tomcat drop the connection before the client read the 413; there was no rate limit.
- Decision:
  - config/docs.yaml is the one source: the gateway derives its multipart caps from `limits.max_file_bytes` (one file a little over it still reaches the controller, which refuses it with an audit record; a request over `max_file_bytes` + 2 MB cannot be parsed), and gains `limits.uploads_per_minute` (20).
  - `AttachmentGate`, in the security chain after authentication and before any body is read: upload, list, status and download need `cap:docs` (403 `forbidden`); an upload whose declared length passes the request cap is 413 `file_too_large`; an upload past the caller's per-minute allowance (a sliding minute per tenant and user, in the gateway's memory) is 429 `upload_rate_limited`, a new error code with its Arabic and English messages in every catalog. Each refusal is audited fail-closed.
  - `server.tomcat.max-swallow-size: -1`: the rest of a refused body is read and discarded, so the client gets the JSON 413; a chunked body over the cap gets it from the multipart handler. A test sends 40 MB over a real connection both ways.
  - The ingestion of an object over the limit fails as `file_too_large` (ADR-0176).
- Consequences: A user without `cap:docs` no longer reads the list of their own earlier uploads. Reading and discarding an oversize body costs the gateway that bandwidth; the per-user allowance and authentication bound who can make it read one. Several gateway processes would each keep their own allowance.

### ADR-0178: One MinIO user per service; the root credentials stay in minio-init
- Date: 2026-10-12
- Phase: 04 (owner decision S7; supersedes ADR-0151's "MinIO is reached with the root credentials" and ADR-0022's for Langfuse)
- Status: decided
- Context: The agent, the gateway and Langfuse all used MinIO's root credentials.
- Decision: `minio-init`, the only holder of the root credentials besides MinIO itself, makes the buckets, four policies and four users from `.env`: `gateway-uploads` (put, get and delete in `uploads`: it stores, downloads and discards an unregistered upload), `ingest-uploads` (get in `uploads`), `sweeper-uploads` (list `uploads`, delete in it; it never reads a document), `langfuse-events` (its own `langfuse` bucket). Each policy also allows the bucket's location, which the clients ask first. The agent gets no MinIO credentials: since ingestion moved to the ingest service (ADR-0176) it reads no upload. A test proves each user can do its part and nothing else, and a smoke check fails when any container but MinIO holds the root password. `scripts/init_env.sh` generates the new secrets; an existing `.env` needs the eight `MINIO_*_ACCESS_KEY` / `_SECRET_KEY` lines of `.env.example` (generated into the development `.env` in this session).
- Consequences: The owner decision said "agent and ingest: read uploads"; the agent's read access is left out because it has no use for it. A leaked gateway key can write uploads but not list them; a leaked sweeper key cannot read one.

### ADR-0179: `make up` never depends on `make models`; the reranker's weights are pinned and kept in an external volume
- Date: 2026-10-12
- Phase: 04 (owner decision R1; amends ADR-0134)
- Status: decided
- Context: LiteLLM waited for the `rerank` service to be healthy, which it never was without weights, so `make up` failed until `make models` had run, while README §21.2 said to skip `make models` for a remote endpoint. The download took the repository's latest revision; `make clean` deleted the 2.3 GB volume; the reranker's model and endpoint were written in infra/litellm/config.yaml.
- Decision:
  - LiteLLM no longer depends on `rerank`. Without weights, `rerank` idles, logs what to run (`make models`, or `retrieval.rerank.enabled: false`) and reports healthy; a docs search then fails visibly until the weights exist (ADR-0175). `scripts/preflight.sh` creates the volume and warns when the weights are missing or not the pinned revision.
  - `make models` downloads `BAAI/bge-reranker-v2-m3` at revision `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e` (the one measured in phase 04) and checks it arrived; it skips the reranker when `RERANK_API_BASE` is not the stack's `rerank` service.
  - `rerank-models` is an external volume: `make clean` keeps it.
  - `RERANK_MODEL` and `RERANK_API_BASE` come from `.env`, like `EMBED_MODEL` and `EMBED_API_BASE` (defaults `infinity/bge-reranker-v2-m3`, `http://rerank:7997`).
  - README §21.2 and PROGRESS give the order: `make models`, then `make up`.
- Consequences: A new revision of the reranker is an explicit change of the pin, measured like a model change. Removing the weights is `docker volume rm rerank-models`.

### ADR-0180: The weak tests the review named are replaced
- Date: 2026-10-12
- Phase: 04 (owner decision Q3)
- Status: decided
- Context: Review round 1 named tests that could not fail or did not test what they claimed.
- Decision:
  - `test_rag_extract.py`: the presentation-form test asserted an inequality that held for any output; the PDF test checked `"2031" in text`. Both now compare whole strings and whole lines (with the fixtures of ADR-0173).
  - `test_capability_runtime_isolation.py`: besides the test-only capabilities, the real `docs` and `general` run in one turn (the router chooses both), with `docs` wrapped to write `general`'s namespace through its output and through its RuntimeContext copy; `general`'s slice stays untouched and the write stays inside `docs`' own slice.
  - `AttachmentProxyTest`: beyond the agent's mocked 404, the gateway asks the agent as the caller whatever identity headers the caller sends, at the caller's own conversation path; a refused download never reads storage although the object exists; list and status never carry the object key.
  - `test_retention.py`: the comment that said the mock embeds every text alike (untrue since ADR-0143) is corrected.
  - The tests of the removed retention paths (delete, expiry, touch, purge) are gone with them (ADR-0166).
- Consequences: None beyond the tests.

### ADR-0181: Abstention needs a whole phrase and no source line; numbers match whole; the Ragas reference names no source
- Date: 2026-10-12
- Phase: 04 (owner decision Q2; supersedes those parts of ADR-0154)
- Status: decided
- Context: Abstention scored 1.00 while two or three of its six cases showed a source line in every run, because the scorer matched phrases anywhere and scored the source line elsewhere; `contains_any` let "40" match 140 or 2040; f5fc687 widened three labels.
- Decision:
  - An abstention counts only when the answer says so in a whole phrase (word boundaries, after `fold()`) and shows no source line.
  - `contains_any` matches a number only as a whole number. Writing this test found `fold()` deleting Arabic-Indic digits: its tashkeel range U+064B–U+0670 covered U+0660–U+0669. The range now stops at U+065F (plus U+0670), so «٤٠» folds to "40" as intended.
  - The labels of f5fc687 are kept, each justified: «أربعين» and «اربعين» are the accusative and genitive of «أربعون», the same number (policy-training); «لا توجد اي معلومات» is the listed «لا توجد معلومات» with «أي»; «لا يمكن تحديد» says the document does not settle the question, an abstention.
  - An answer carrying a capability label (`[docs]`) or a bracketed file name fails its case (owner decision C1).
  - Answers no longer name their sources (ADR-0163), so the Ragas reference is the case's reference answer alone, without the source label ADR-0154 appended; contexts keep the label the answer model saw in its delimiter.
  - `make test-evals` covers each rule.
- Consequences: Abstention now fails on a source line, so the C1 fix (question words dropped before attribution) is what keeps it above its threshold.

### ADR-0182: Attribution in two steps: the question's words decide whether a chunk may be cited, rarity decides which
- Date: 2026-10-12
- Phase: 04 (owner decision C1; supersedes the attribution rule of ADR-0156 and ADR-0163)
- Status: decided — Superseded by ADR-0183 on 2026-10-12 (the scoring and the coverage measured on live turns)
- Context: The first Q1 run (make-eval-20261012T105801Z, rerank 16/1070) scored citation page accuracy 0.69, below its 0.85 threshold. Dropping the question's words before forming n-grams (ADR-0163) left a short answer with too few bigrams to reach three ("مدة الإجازة المرضية براتب كامل هي ثلاثون يوماً" keeps only "ثلاثون يوما" once the question's words go), so it cited nothing. Dropping only the n-grams made of question words cited all twenty pages of twenty-pages.pdf, whose lines differ only in the department and article numbers the question itself gives. An abstention naming the document ("لا توجد معلومات في السيرة الذاتية ...") cited the CV's title page.
- Decision: `config/citations.yaml` `attribution: {ngram: 2, min_shared_ngrams: 1, min_new_word_coverage: 0.25, relative_to_best: 0.6}`, applied in two steps:
  - Eligible (the owner's rule): the question's words are dropped from the answer first. A chunk may be cited only when it shares at least one answer bigram not made only of question words, and holds at least a quarter of the answer's words that are not in the question. An answer that restates the question, or adds only its own phrasing around the document's name, cites nothing.
  - Cited: each eligible chunk is scored by all the answer's bigrams it shares, each weighted by one over the number of retrieved chunks that hold it. It is cited at 0.6 of the best score or more. Boilerplate every page repeats counts for little; the bigram naming the page's own article and department counts most. 0.6, not 0.5, so a page that ties at exactly half the best (two pages sharing "ثلاثون يوما براتب كامل") is not cited.
  - Still deterministic and capability-agnostic, with no model call and no change to the answer.
- Measured offline, before the rerun, with `attribute` on the eval's six documents (every page a candidate, which is harsher than the retrieved top 8): 56 answers, made of the reference answers with a page expectation and the run's answers as the log prints them (first 70 characters). ADR-0163's rule: 38/56. Dropping question-only n-grams: 42/56. This rule: 55/56. The one miss is a log answer cut before its fact. relative_to_best 0.5–0.8 give the same 55/56.
- Consequences: The question's words still decide whether a source line shows, so "not found" answers show none. Among the chunks that pass, the question's words help pick the page, which is what tells templated pages apart. `app/capabilities/docs/tests/test_docs.py` covers the templated pages, the short answer and the abstention that names the document.

### ADR-0183: Attribution scores a chunk by its best sentence with rarity weights; coverage is per answer sentence; "not found" is said in one sentence
- Date: 2026-10-12
- Phase: 04 (owner decision C1; supersedes ADR-0182's scoring and coverage)
- Status: decided
- Context: ADR-0182 was measured offline with whole pages as chunks and answers as the log prints them. The live run on it (make-eval-20261012T113452Z, rerank 16/1070) scored citation page accuracy 0.76 and abstention 0.67. The real twenty-pages.pdf chunks hold about fifteen lines each, two per page, and article numbers repeat on neighbouring pages (page 3 holds articles 31 to 51, page 4 holds 41 to 61). So an answer's numbers matched page 4 too, on other lines, and 1/n weights left the neighbours above 0.6 of the best: "المادة 46 ... رقم 3 ... كل 17" cited pages 2, 3 and 4. A quarter coverage let one loose bigram ("اللائحة على") cite page 1 under "لا توجد معلومات في الملف ... حيث يقتصر محتوى اللائحة على ...". The cv-hospital answer abstained and then described the CV's other experience, which page 2 holds.
- Decision: `config/citations.yaml` `attribution: {ngram: 2, min_shared_ngrams: 1, min_new_word_coverage: 0.35, relative_to_best: 0.6}`:
  - Eligibility keeps ADR-0182's owner rule: the question's words are dropped first, and a chunk shares at least one remaining bigram. Coverage is now per answer sentence: the chunk holds at least 0.35 of the non-question words of one sentence of the answer. An answer drawing one fact from each of several chunks still cites each chunk.
  - Score: a chunk scores by its best sentence (split at line ends and . ! ? ؟). A sentence scores the sum, over the answer bigrams it holds, of log((N + 1) / n), where N is the number of retrieved chunks and n the number holding that bigram. A bigram every retrieved chunk holds weighs almost nothing. The page whose one line holds the department, the article and the weeks together outscores a neighbour that holds them on separate lines.
  - `docs.evidence` 2.1.0 (ar, en): when the passages do not contain the answer, the model says so in one sentence and does not describe what else the file covers. A "not found" answer then holds nothing a page could be cited for.
  - Measured with `attribute` on the 65 live turns of today's two runs (answers, retrieved chunks and their texts read from the checkpoints and agent.rag_chunks), against the case expectations:
    - ADR-0182: 52/65.
    - Sentence scoring with 1/n weights: 52/65.
    - Sentence scoring with log weights at coverage 0.25: 61/65.
    - This rule: 63/65. The two misses are cv-hospital, whose answer drew on page 2 before the prompt change.
- Consequences: No model call and no change to the answer text. A "not found" answer gets shorter: one sentence. The rule needs the chunk text, which the answer step already fetches (ADR-0162). Tests: the templated pages with overlapping numbers, the short answer adding only the number, the restating abstention, and the leave-housing abstention, which a quarter coverage cites and this rule does not. The answer drawing on three chunks still cites all three.

### ADR-0184: The reranker is off by default; switched on, it scores 16 candidates on their first 1070 characters
- Date: 2026-10-12
- Phase: 04 (owner decision Q1; supersedes ADR-0157)
- Status: decided — Superseded by ADR-0187 on 2026-10-13 (where the setting lives: `.env`, per environment, not config/docs.yaml; the values and the measurement stand)
- Context: ADR-0157 cut the reranker to 16 candidates on their first 400 tokens to bring `rag.retrieve` from 27 s to 6 s, without measuring what the cut cost. The owner asked for three rag runs for each of three configurations, with twenty-pages.pdf cases whose answers lie past the first 400 tokens of their chunk, and for the fastest configuration that meets every rag threshold. The cases are six `twenty-past-truncation-p*` cases in rag.jsonl, answers on line 16 of pages 3 to 18, past 1070 characters.
  - The 400-token bound is now a character bound, 1070 (`retrieval.rerank.max_chars`): 400 tokens of the reranker's own tokenizer at the fewest characters per token measured on the eval documents (2.68; median 3.54). A character bound needs no tokenizer in the agent.
  - The runs use ADR-0183's attribution, the OpenRouter development tiers (ADR-0065) and this host's CPU reranker. Thresholds: hit@8 0.90, citation page accuracy 0.85, faithfulness 0.85, answer correctness 0.80, abstention 0.90.
- Decision: `config/docs.yaml` `retrieval.rerank: {enabled: false, candidates: 16, max_chars: 1070}`. Retrieval keeps vector top 30 and full-text top 30, fuses them by RRF and takes the top 8 in fusion order. The reranker stays deployed: `enabled: true` brings back 16 candidates on 1070 characters, the faster of the two rerank configurations, which also met every threshold.
- Measured, three rag runs each (53 cases; Langfuse dataset runs named in PROGRESS):

  | configuration | hit@8 | citation | faithfulness | correctness | abstention | retrieve p50 | retrieve p95 |
  |---|---|---|---|---|---|---|---|
  | rerank 30, full text | 1.00, 1.00, 1.00 | 1.00, 1.00, 0.94 | 0.93, 0.96, 0.94 | 0.89, 0.89, 0.90 | 1.00 ×3 | 0.74–0.78 s | 26.0–27.0 s |
  | rerank 16 / 1070 chars | 1.00, 1.00, 1.00 | 1.00, 1.00, 0.97 | 0.95, 0.96, 0.91 | 0.90, 0.92, 0.91 | 1.00 ×3 | 0.75–0.76 s | 5.6–5.7 s |
  | rerank off | 1.00, 1.00, 1.00 | 0.91, 0.91, 0.94 | 0.93, 0.97, 0.96 | 0.89, 0.89, 0.89 | 1.00 ×3 | 0.13 s | 0.14–0.15 s |

  - On the six past-truncation cases, 16/1070 gave a wrong number in each run (the neighbouring line's 16 for 17, p15 twice and p9 once). Full text and off gave none.
  - Off's misses are citations: one-line answers ("كل 17 أسبوعا") that cannot single out a templated page, and one full answer citing its neighbours.
- Consequences: Retrieval is about 0.6 s faster at p50 and 5 s faster at p95 than ADR-0157, and the reranker's CPU is idle. The measurement is on six small documents, of at most 40 chunks, where hybrid fusion alone puts the answer in the top 8 every time. A collection of many or long files can need the reranker, and a GPU-served reranker would make the full configuration affordable. Both are a configuration change, to be measured by the rag suite on that collection. Full run 2 was repeated: its first attempt stopped after 16 cases on one gateway 502 `storage_unavailable` (a SocketException to MinIO, the only one in the day's logs).


### ADR-0185: A refused upload never holds a connection; the upload gate's order is cap:docs, rate, size, concurrency
- Date: 2026-10-13
- Phase: 04 (owner decision review round 2, finding 2)
- Status: decided
- Context: Review round 2 found two ways one user could stop chat for everyone. The first: `server.tomcat.max-swallow-size: -1` made the gateway drain the whole declared body of a refused upload, so a client declaring `Content-Length: 10^12` and trickling bytes held a connection (and its request-processing thread) open indefinitely; enough of them exhausted Tomcat's connections. The size check also ran before the rate limit, so a flood of oversize uploads was never rate-limited, and there was no cap on concurrent uploads per user.
- Decision:
  - `server.tomcat.max-swallow-size` is set to `0` — the gateway reads none of a refused request's body — and `AttachmentGate` sets `Connection: close` on every refusal of the upload path (401, 403, 413, 429) and on an unauthenticated upload, so Tomcat closes the socket as soon as the refusal is written rather than reading a body the gateway already refused. (A positive bound such as 2 MB was tried first; Tomcat still issues a blocking read for up to that many bytes before closing, so a trickled body held the socket until the upload timeout. `0` both never drains and closes at once, which is what the review asked for.) `connection-upload-timeout` (30 s) cuts a stalled body on an *accepted* upload.
  - The gate's order for an upload is: authentication and `cap:docs`; then the per-user rate limit, which counts every attempt that reaches it, refused or not; then the declared `Content-Length` against the limit; then a per-user concurrent-upload cap (`limits.max_concurrent_uploads`, default 2), 429 `too_many_uploads`. The concurrency slot is held for the whole request — while the body is read — and released when it completes, so one user can tie up at most that many body-reading threads.
  - A new error code `too_many_uploads` (429) with an Arabic and an English message.
- Consequences: A refused upload's connection closes at once rather than being held; a user's uploads cannot starve the connection pool. A client that streams a body larger than the limit without honouring `Expect: 100-continue` sees the connection closed mid-send rather than a readable 413 — the anti-starvation trade the review chose; the web composer caps file size client-side (20 MB) and so never sends a body the gateway would refuse by size, and a compliant client (curl, and the browser's preflighted size check) gets the JSON 413. Tests: a client declaring 10^12 bytes and trickling gets the JSON 413 and its socket closed within 5 s; a refused attempt counts toward the rate limit; a third concurrent upload is refused `too_many_uploads`; a chat turn completes while 200 refused upload connections are attempted; smoke's over-limit uploads use `--expect100-timeout` so the 413 is read before any body is sent. The per-user limits are in this gateway's memory (one process in v1, ADR-0109).

### ADR-0186: The gateway parses no document; type is magic bytes and archive header only
- Date: 2026-10-13
- Phase: 04 (owner decision review round 2, finding 3)
- Status: decided
- Context: The gateway opened every uploaded PDF with Apache PDFBox (to count pages for the limit) and walked DOCX/XLSX ZIP entries, both in its own JVM, which also serves every chat turn, with no memory or time bound. A PDF whose cross-reference stream expands beyond the heap could exhaust the gateway's memory and stop chat for everyone — the same class of risk ADR-0176 moved out of the agent, left in the gateway.
- Decision:
  - The gateway parses and decompresses nothing. `UploadInspector` checks the type by magic bytes — `%PDF` for a PDF; the ZIP local-file-header magic plus a `word/` or `xl/` marker found by a bounded raw byte scan (never an inflate) for DOCX/XLSX; no NUL byte for text — mirroring the agent's `detect_kind`. PDFBox and every document-parser dependency are removed from `build.gradle.kts`.
  - The page limit (D2) and every content bound are enforced only in the ingest service's sandboxed child process (ADR-0176), which already fails an over-limit PDF with `too_many_pages` and its Arabic message. The gateway no longer returns `too_many_pages`, counts no pages, and records no `pages` in the upload audit; `/v1/attachments/limits` still reports `max_pdf_pages` so the composer can warn before an upload.
- Consequences: An uploaded document is read in exactly one place, the sandbox, so no file can exhaust the gateway. A malformed or bomb PDF is accepted by the gateway (valid magic) and fails in ingest with its reason while chat is unaffected. Tests: a build check that no PDF/POI/Tika/iText class is on the gateway classpath; the page-limit refusal moves from the gateway test to the ingest tests. OpenAPI: the upload 413 description drops `too_many_pages` and a 429 is documented (regenerated; within the unmerged 2.0.0, ADR-0046).

### ADR-0187: The reranker is configured per environment, from .env, not config/docs.yaml
- Date: 2026-10-13
- Phase: 04 (owner decision review round 2, finding 3; supersedes the default and the location of ADR-0184)
- Status: decided
- Context: ADR-0184 put `retrieval.rerank` (enabled, candidates, max_chars) in config/docs.yaml with `enabled: false`. The reranker's cost is a property of the host (seconds per query on CPU, affordable on a GPU), not of the code, so where it runs should choose it, as the reranker's model and api_base already do (ADR-0179, R1).
- Decision: `RERANK_ENABLED`, `RERANK_CANDIDATES` and `RERANK_MAX_CHARS` come from the environment (`Settings`, passed to the agent by compose). `build_documents` overrides `DocsConfig.retrieval.rerank` with them, so config/docs.yaml carries no `rerank` block. The development default is off (hybrid fusion alone met every rag threshold, ADR-0184); `.env.example` documents the production setting — on, 16 candidates, 1070 characters, a GPU host. The eval harness reads the same env for its rag summary, so the summary names what actually ran. The measured numbers of ADR-0184 stand (citation accuracy off 0.91–0.94, 16/1070 0.97–1.00).
- Consequences: Turning the reranker on in production is an `.env` change and a restart, not a code or config-file edit, and development never pays its latency. The values are no longer in a file under review; `.env.example` is the reference. No interface change: `Retrieval.rerank` keeps its shape, now filled from settings.

### ADR-0188: Eval records are written to build/evals and survive make clean
- Date: 2026-10-13
- Phase: 04 (owner decision review round 2, finding 4)
- Status: decided
- Context: `make eval`'s only durable record was its Langfuse dataset run. `make clean` deletes the Langfuse volumes, and the round-1 session's `make clean` destroyed the nine Q1 dataset runs named in PROGRESS, so their per-case results and case counts can no longer be verified. The printed rag summary's `(n)` was the count of retrieval spans, not cases, which read as a case count.
- Decision: `run_evals.py` writes each run's per-case results and suite scores to `build/evals/<run id>.json` (gitignored) and prints the path; the file holds, per suite, the pass/fail and problems of every case, the rag or guard metrics, the recorded case count and the rerank configuration. The rag summary now prints "N retrieval spans, M cases recorded" so the two counts are not confused, and PROGRESS's rag tables relabel the `(n)` column to "retrieval spans" and state each run's recorded case count. The per-minute upload limit is read from `.env` (`UPLOADS_PER_MINUTE`, dev value high) so `make eval`, which uploads a document per rag/routing case, never sleeps on a 429.
- Consequences: A run's results outlive its Langfuse dataset and `make clean`, so a claim in PROGRESS can be checked against `build/evals/<run>.json`. The file is local only (gitignored, like the rest of `build/`); it is not a substitute for Langfuse traces, which still carry the per-case `eval_pass` score.

### ADR-0189: The upload gate matches the path the router routes on; the firewall rejects any path not written plainly
- Date: 2026-10-14
- Phase: 04 (owner decision, verification of review round 2)
- Status: decided
- Context: The verification of round 2 found that `POST /v1/conversations/{id}/%61ttachments` reached the upload handler without the gate: `AttachmentGate` matched its pattern against the raw `getRequestURI()`, while Spring MVC matches handlers on decoded path segments, and Spring Security's strict firewall does not reject an encoded letter. Reproduced on the running stack: with two uploads holding the caller's slots, encoded uploads were admitted (no `too_many_uploads`), and 20 of them in the minute after the allowance was spent got no `upload_rate_limited`. Every per-user upload bound — cap:docs at the gate, rate, declared size, concurrency — was off on that spelling, so one user could hold any number of trickling uploads (ADR-0054's threat: one user denying chat to others).
- Decision:
  - `AttachmentGate` decides which endpoint a request is on from the lookup path Spring MVC routes on: the path within the application, each segment decoded and stripped of matrix parameters (`PathContainer.PathSegment#valueToMatch`), never the raw URI.
  - A gateway firewall (`PathFirewall`, an `HttpFirewall` bean in front of Spring Security's `StrictHttpFirewall`) rejects with 400 `invalid_request` (JSON envelope, `Connection: close`) any request whose raw path holds `%` (so every percent-encoded and double-encoded sequence, upper- or lower-case hex), `;`, a backslash, `//`, or a `.` or `..` segment. No platform path needs encoding. It applies to every path, not only `/v1/**`, because deciding whether a raw path is under `/v1` could itself be fooled by an encoded spelling; the query string is not checked.
- Consequences: Both layers would each close the bypass alone. A client that percent-encodes a path character the platform never uses now gets 400 instead of routing. Tests (`UploadPathTest`, raw sockets): `%61ttachments`, `%2561ttachments`, `attachme%6Ets`, `attachme%6ets`, `//attachments`, `/./attachments`, `attachments;x=1`, a trailing slash and the plain path — without cap:docs each is 400 or 403; with cap:docs and the caller's slots held each is 400 or 429 `too_many_uploads`, never the handler; the gate's lookup path decodes `%61` and drops `;x=1`.

### ADR-0190: A gateway-wide cap on uploads in flight and a deadline for receiving an upload's body
- Date: 2026-10-14
- Phase: 04 (owner decision, verification of review round 2)
- Status: decided
- Context: ADR-0185's per-user cap bounds one user, not all uploads together, and Tomcat's `connection-upload-timeout` is per read, so a body trickled a byte at a time held its slot for as long as it kept trickling. With virtual threads the shared resource is connections (Tomcat's `max-connections`, which the gateway inherited unset).
- Decision:
  - `limits.max_concurrent_uploads_total` (config/docs.yaml, 50; 50 when absent) caps uploads in flight across the gateway, checked after the per-user cap; past it, 429 `too_many_uploads` (the same code and message as the per-user cap: "too many uploads at once") with `Connection: close`. In this process's memory, like the per-user cap (ADR-0109).
  - `limits.upload_deadline_seconds` (120; 120 when absent): an admitted upload's body must arrive within that long of admission at the gate; past it, 408 `upload_timeout` (new code, Arabic and English messages, web i18n, OpenAPI) with `Connection: close`, and its slots are released. Tomcat's own multipart parsing reads its internal stream, which no request wrapper can bound, so the gate's `DeadlineUploadRequest` parses the parts itself with Tomcat's parser and the same limits (the multipart config; 512-byte part headers and 10 parts, Tomcat's defaults) from a stream that refuses every read past the deadline, and removes the parts' temporary files when the request ends. A read already blocked when the deadline passes ends at the latest after `connection-upload-timeout` (30 s), so a body is held at most deadline + 30 s.
  - `server.tomcat.max-connections: 8192` and `accept-count: 100` are written in application.yml: Tomcat's defaults, made explicit so they are reviewed; uploads can hold at most 50 of the connections.
  - The agent's `Limits` model accepts the two new keys (gateway only, as `max_concurrent_uploads`).
- Consequences: All uploads together hold at most 50 connections for at most 150 s each, whatever their number of users. On one gateway, 50 legitimate uploads at once is the most the gateway serves; the 51st waits for a 429 retry. The 408 is not audited, like the multipart 413 (both are written by the exception handler before the controller). Tests (`UploadCeilingTest`, config copied with a cap of 2 and a 4 s deadline): one user holding the whole cap with two trickling uploads, another user's upload is 429 `too_many_uploads` with a close and a third user's chat turn completes while both are still in flight; a trickled upload gets 408 `upload_timeout` with a close between 4 and 8 s and both its slots are free again.

### ADR-0191: The upload rate's default is the strict one: absent from .env, the gateway uses config/docs.yaml
- Date: 2026-10-14
- Phase: 04 (owner decision, verification of review round 2; refines ADR-0188's `UPLOADS_PER_MINUTE` fallback)
- Status: decided
- Context: Compose passed `GATEWAY_DOCS_UPLOADS_PER_MINUTE: ${UPLOADS_PER_MINUTE:-60}`, so a deployment whose .env lacked the variable ran at 60 uploads per minute per user, three times the baseline of 20 in config/docs.yaml.
- Decision: Compose passes `${UPLOADS_PER_MINUTE:-}`: no fallback value, so an absent variable reaches the gateway empty, and an empty or absent `GATEWAY_DOCS_UPLOADS_PER_MINUTE` binds to no value, so the gateway uses config/docs.yaml's `uploads_per_minute` (20). The empty `:-` form rather than a bare `${UPLOADS_PER_MINUTE}` only avoids compose's "variable is not set" warning on every command; the value passed is the same, empty (assumed). `.env.example` sets the development value, 60. `scripts/smoke.sh` expects 20 when .env does not set it. The `GatewayProperties` javadoc names the variable correctly, `GATEWAY_DOCS_UPLOADS_PER_MINUTE`.
- Consequences: A .env created before ADR-0188 (without the variable) now runs at 20 uploads per minute; `make eval` then waits out a 429 when it uploads faster (it already retries after 61 s), so development .env files should copy `UPLOADS_PER_MINUTE=60` from `.env.example`. `max_concurrent_uploads`, `max_concurrent_uploads_total`, `upload_deadline_seconds` and the size limit have no environment override; their defaults when absent from config/docs.yaml are 2, 50 and 120 s, and a missing `max_file_bytes` or `uploads_per_minute` stops the gateway at start. Test (`UploadRateDefaultTest`, bound from environment variables): unset → 20, empty → 20, `60` → 60, the shipped baseline is 20.

### ADR-0192: Conversation history is read from the checkpointed thread; the list comes from the agent's ownership table
- Date: 2026-10-15
- Phase: standalone PR before phase 05 (owner decision, 2026-10-15)
- Status: decided
- Context: The web page had no history: every chat started empty and a reopened `/chat/<id>` showed nothing. README §6.1 item 5 names conversation CRUD, but no phase built it. The agent keeps every exchange: `persist_state` appends the question and the post-guard reply to `AgentState.messages`, and the rolling summary only moves `summary_cursor`, so the latest checkpoint of a thread holds its whole history. The hardening backlog's checkpoint pruning keeps the latest checkpoint per conversation.
- Decision:
  - The history of a conversation is the latest checkpoint of its thread, read with `graph.aget_state`. No second message store.
  - The list of a user's conversations comes from `agent.conversation_owners`, which gains `title` (the conversation's first question as stored, at most 80 characters) and `last_turn_at`. The runner sets both after a turn ends with `done`; the title is set once.
  - Rows from before this change are filled on demand: listing a caller's conversations reads, for at most 50 of their rows without a title, the thread's first question and the time of its latest checkpoint. A thread with no exchange gets an empty title, so it is not read again, and is not listed until a turn finishes in it.
  - A conversation is listed once a turn has finished in it. A conversation that holds only uploads is not listed.
- Consequences: Every existing conversation, old ones included, shows its full history. The list is one indexed query per page once filled. If checkpoint pruning ever removes the latest checkpoint of a thread, the history goes with it; the backlog item already keeps it.

### ADR-0193: What the history shows
- Date: 2026-10-15
- Phase: standalone PR before phase 05 (owner decision, 2026-10-15)
- Status: decided
- Context: A persisted reply carries `content` (the post-guard text every later model call reads), and, when a DATA redaction changed the draft after it streamed, `shown_answer` (the streamed text, audit metadata only, ADR-0048). A reply can be retracted when its audit record failed (ADR-0048, ADR-0076). Failed turns are never persisted.
- Decision:
  - Each message returns `role`, `text` (`content`), and for a reply its `turn_id`. `shown_answer` and `retract_reason` never leave the agent.
  - A retracted exchange is left out as a pair, question included, as Context Assembly leaves it out (ADR-0076).
  - From now on `persist_state` keeps the turn's attachment ids on the question and the turn's citations on the reply. The history returns the names of those files (from `agent.attachments`, this conversation's only) and the citations as the `citation` events carried them. Messages persisted before this change have neither.
  - No rename, no delete: the product deletes nothing (ADR-0166).
- Consequences: A user sees what the model may see again, which after a late redaction is the redacted text rather than what streamed. The extra fields on persisted messages are never read by Context Assembly, which reads `content` only.

### ADR-0194: Conversation API: two audited reads, owner only, at the gateway and the agent
- Date: 2026-10-15
- Phase: standalone PR before phase 05 (owner decision, 2026-10-15)
- Status: decided
- Decision:
  - Gateway `GET /v1/conversations?limit&before`: the caller's conversations, newest `last_turn_at` first; `limit` 1–100, default 30; `before` an ISO-8601 instant for the next page; the reply has `next_before` when more remain. Gateway `GET /v1/conversations/{id}/messages`: that conversation's messages, oldest first.
  - The gateway names the caller from the verified token only (X-User-Id, X-Tenant-Id, ADR-0059). It checks ownership against `gateway.conversations`, and the agent checks again against `agent.conversation_owners`: another owner's conversation is 403 `conversation_forbidden` at both; a conversation nobody has used is an empty list.
  - Both are audited fail-closed like memory and attachments (ADR-0042): actions `conversation.list` (detail: count) and `conversation.read` (conversation id; detail: message count). No message text in the audit.
  - API version 2.0.0 → 2.1.0 in both services (additive; CLAUDE.md, ADR-0046).
- Consequences: The web page lists conversations and reopens them. Admins have no access to other users' conversations through this API.


### ADR-0195: Guard policies on MCP results run at runtime, per principal (D1)
- Date: 2026-10-15
- Phase: 05 (owner decision D1)
- Status: decided
- Context: MCP servers return content the platform did not write. ADR-0158 put every guard policy at runtime, per principal, never at ingestion or inside a backend.
- Decision: No guard policy runs at ingestion, inside a document backend or inside an MCP server. An MCP result is a tool output: it passes POST_TOOL (sanitize_untrusted first), then OUTPUT and DATA on what leaves, for the principal who asked. Reference servers apply no policy.
- Consequences: Role exceptions (ADR-0107) keep working for MCP content as for documents. An MCP server cannot weaken or replace a platform policy.

### ADR-0196: Capabilities stay independent over MCP; one capability per integration (D2)
- Date: 2026-10-15
- Phase: 05 (owner decision D2)
- Status: decided
- Context: An "all MCP tools" capability would lose per-integration permissions, routing, tier, policies and evals (BACKLOG, MCP principles).
- Decision: A capability's context comes only from its manifest (ADR-0161) and it calls only the tools it declares (ADR-0160), MCP tools included. Each integration is its own capability with its own `cap:{slug}`, routing hints, prompts and evals; there is never a generic capability exposing every MCP tool.
- Consequences: Adding an integration is a capability folder, an allowlist entry and a role, reviewed together.

### ADR-0197: Every MCP call goes through the broker (D3)
- Date: 2026-10-15
- Phase: 05 (owner decision D3)
- Status: decided
- Context: CLAUDE.md rule 6; the broker is where scope injection, PRE_TOOL, POST_TOOL, audit and the manifest's declarations are enforced.
- Decision: MCP tools are broker tools from an MCP tool provider. No capability imports or reaches the MCP client; the capability lint refuses an import of `app.mcp`. The MCP document backend reaches its server through the same client, behind `docs_search`, the broker tool the docs capability declares.
- Consequences: Every MCP call is guarded, timed, audited and declared like any first-party tool.

### ADR-0198: MCP servers and their tools are an allowlist in config/mcp.yaml (D4)
- Date: 2026-10-15
- Phase: 05 (owner decision D4)
- Status: decided
- Context: Runtime discovery would let a server add tools no one reviewed.
- Decision: `config/mcp.yaml` lists the servers and the tools allowed from each, versioned and reviewed by PR. A tool a server lists but the file does not name is never registered. There is no admin UI for MCP servers.
- Consequences: A new server or tool is a PR to `config/mcp.yaml`, with the schema hash of each tool (ADR-0206).

### ADR-0199: Internal servers in every profile; external servers off, never under airgap (D5)
- Date: 2026-10-15
- Phase: 05 (owner decision D5)
- Status: decided
- Context: CLAUDE.md forbids a dependency on external SaaS; the owner's internal services must work air-gapped.
- Decision: A server marked `internal: true` is allowed in every profile, air-gapped included. No external server is shipped; external servers are disabled by default, never available in the airgap profile, and enabling one is a later owner decision per server (ADR-0216 states the mechanism).
- Consequences: `config/mcp.yaml` ships the two reference servers, both internal.

### ADR-0200: Read-class MCP tools only; a write tool is refused at startup; no delete tool ever (D6)
- Date: 2026-10-15
- Phase: 05 (owner decision D6)
- Status: decided — Superseded by ADR-0230 on 2026-10-18
- Context: Approvals for write-class tools arrive in phase 11; the product deletes nothing (ADR-0166).
- Decision: Every allowlisted tool is `risk: read`. A tool marked `risk: write` stops the agent at startup with a clear error until approvals exist. No delete tool is ever allowlisted: a tool whose name or remote name holds `delete`, `remove`, `drop` or `purge` is refused by validation.
- Consequences: The reference servers expose read tools only (the RAG server's `register` writes its own index of a file the platform already stored; it is called by the document backend as transport, never declared to a capability; see ADR-0212).

### ADR-0201: A partly scanned PDF ends indexed with a warning (D7)
- Date: 2026-10-15
- Phase: 05 (owner decision D7; amends the partly scanned part of ADR-0128)
- Status: decided
- Context: A PDF whose average text passes `extraction.min_text_chars_per_page` was indexed from its text pages, and nothing told the user that some pages were not read (BACKLOG).
- Decision: Pages under `extraction.min_text_chars_per_page` are counted. The file still ends `indexed` (a whole file under the threshold still fails `scanned_unsupported`), and its status carries `warnings: [{code: pages_without_text, count}]`. The `DocumentBackend` status contract gains `warnings`; the contract suite covers it on every backend. The gateway passes the field through and the web badge says, for example, «جاهز — ٥ صفحات صور لم تُقرأ». The pages themselves are still not read: OCR is v2.
- Consequences: Agent migration 0012 stores the warnings on the attachment record. API version 2.2.0 (ADR-0214).

### ADR-0202: DocumentScope names collections; the broker builds it; evidence is fetched per source (I1)
- Date: 2026-10-15
- Phase: 05 (implementation choice I1; BACKLOG MCP item 10; amends ADR-0159 and ADR-0160 on the scope's shape)
- Status: decided
- Context: The scope was (tenant, user, conversation), one backend served every capability, and the answer step fetched every source through it: kb (phase 08) and an MCP backend would each need a core change.
- Decision:
  - `DocumentScope` and `ToolScope` name `tenant_id`, `user_id` and `collections`. The broker builds the collections from the verified principal and the turn: `sess_{conversation_id}`, plus `kb_{slug}` for each `kb:{slug}:read` the principal holds intersected with the turn's `selected_kbs`. A manifest declares the collection kinds it reads (`documents.collections`, `session` and/or `kb`); the broker keeps only those, so docs (`[session]`) never sees a kb collection.
  - `Source` gains `backend` and `collection`; `graph/evidence.py` fetches each source through the backend and the collection that found it, with a scope holding that one collection after checking it belongs to the turn's scope.
  - `RuntimeContext` carries the backends by name (`documents` is a mapping); `config/<slug>.yaml` `backend` names the one a capability uses, and the broker hands that backend to its tools.
  - The citation event, `done.citations` and the citations persisted on replies (ADR-0193) keep their shape: backend and collection stay internal.
- Consequences: kb (phase 08) is a manifest with `collections: [kb]` and no core change. Tests: the fake backend holding a kb collection (permitted and selected → found; not permitted, or not selected → nothing, by any reference) and a conversation persisted before this change reopening with its citations (G9).

### ADR-0203: MCP transport is Streamable HTTP with the official SDK; server-initiated features refused (I2)
- Date: 2026-10-15
- Phase: 05 (implementation choice I2)
- Status: decided — Superseded by ADR-0246 on 2026-10-19 (server requests are dropped unanswered, not answered with an error)
- Context: stdio would make the agent spawn server processes; server-initiated features would let a server reach back into the platform.
- Decision: The client speaks MCP Streamable HTTP only, with the official Python `mcp` SDK pinned to an exact version (recorded in ADR-0218). `transport: stdio` is refused by config validation; the agent spawns no server process. Sampling, elicitation and roots are refused (the client declares none of them and answers any such request with an error); resources and prompts are never read; `tools/list_changed` is ignored.
- Consequences: A server that only speaks stdio needs an HTTP front of its own, run as its own service.

### ADR-0204: config/mcp.yaml per server and per tool (I3)
- Date: 2026-10-15
- Phase: 05 (implementation choice I3)
- Status: decided
- Decision: Per server: `id` (logical and stable: `routing`, `rag`), `url_env` (the URL comes from `.env`), `auth` (`none` | `bearer_env`: the token's variable name), `internal`, `profiles` where enabled, `timeout_s` (10), `max_result_bytes` (262144), `max_concurrency` (8), and `tools`: `{name, remote_name, risk: read, label {ar, en}, input_schema_sha256, injected {field: principal.tenant_id | principal.user_id | scope.collections}}`. Secrets live only in `.env`.
- Consequences: The file holds no URL and no secret; it is safe to review and to ship.

### ADR-0205: MCP broker tool names are {server}.{tool}; policy scope tool:{server}.{tool} and tool:{server}.* (I4)
- Date: 2026-10-15
- Phase: 05 (implementation choice I4)
- Status: decided
- Decision: An allowlisted tool registers in the broker as `{server}.{tool}` (`routing.geocode`). Policies scoped `tool:{server}.{tool}` apply to it, and `tool:{server}.*` to every tool of the server. A capability declares MCP tools in its manifest `tools` like any tool.
- Consequences: The policy scope grammar gains one wildcard form, for server scopes only.

### ADR-0206: Each allowlisted tool's input schema is pinned by hash; drift disables that tool (I5)
- Date: 2026-10-15
- Phase: 05 (implementation choice I5)
- Status: decided
- Decision: At startup and on every reconnect the client lists the server's tools and compares the SHA-256 of each allowlisted tool's canonical input schema with `input_schema_sha256`. A mismatch or a missing tool disables that tool only (fail closed, logged, listed in `/v1/health`); the others work. `make mcp-hashes` prints the current hashes for the owner to review into the config.
- Consequences: A server cannot change what a tool accepts without a reviewed config change.

### ADR-0207: Injected fields are filled by the broker and hidden from models (I6)
- Date: 2026-10-15
- Phase: 05 (implementation choice I6; ADR-0160 precedent)
- Status: decided
- Decision: A tool's `injected` fields are filled by the broker from the verified principal and the scope, removed from any schema a model or a capability sees, and a call whose arguments name one is refused before anything is sent.
- Consequences: A model or a capability cannot name another tenant, user or collection to an MCP server.

### ADR-0208: MCP results: structured content or text, capped while reading, never cached (I7)
- Date: 2026-10-15
- Phase: 05 (implementation choice I7)
- Status: decided
- Decision: A result is its `structuredContent` when present, else its text parts; images, audio, resource links and embedded resources are dropped with a note. `max_result_bytes` is enforced while the HTTP body is read (`tool_result_too_large`, nothing parsed beyond the cap). Results reach a model only through POST_TOOL (sanitize_untrusted first), inside the untrusted delimiters in the user role (ADR-0165). No MCP result is cached.
- Consequences: A large or hostile result cannot exhaust the agent's memory or reach a prompt unsanitized.

### ADR-0209: Resilience: per-server semaphore, timeout, circuit breaker; a dead server never ends the agent (I8)
- Date: 2026-10-15
- Phase: 05 (implementation choice I8)
- Status: decided
- Decision: Each server has a semaphore (`max_concurrency`), a per-call timeout and a circuit breaker (5 consecutive failures → open 30 s → one half-open probe). An unavailable, timed-out or open-circuit call returns `tool_unavailable` / `tool_timeout` to the capability, which says so in its findings; the turn continues. The agent starts and `make up` stays green with a server down; the client reconnects with backoff. `/v1/health` lists each server and each disabled tool, content-free. The codes have Arabic and English messages in `config/messages/errors.yaml`, the gateway catalogs, `ar.json` and `en.json`.
- Consequences: A slow or dead server costs its own callers a bounded wait and nobody else anything (G7).

### ADR-0210: MCP sessions are pooled per server and carry no per-user state (I9)
- Date: 2026-10-15
- Phase: 05 (implementation choice I9)
- Status: decided — Superseded by ADR-0246 on 2026-10-19 (a pool of sessions per server; I9 stands)
- Decision: One pooled session per server; every call carries all it needs (injected scope included). An expired session is re-initialised.
- Consequences: A server's session id says nothing about who is calling.

### ADR-0211: Two reference MCP servers in the repository (I10)
- Date: 2026-10-15
- Phase: 05 (implementation choice I10)
- Status: decided
- Decision:
  - `mcp-routing-ref`: `geocode {query}` → up to three matches `{name, point, score}`, and `route {from, to, profile: car|foot}` → `{distance_km, duration_min, steps[]}`, over a fixed fixture of about 30 public Riyadh landmarks with Arabic, Gulf and English aliases and an invented road graph; Dijkstra; fixed speeds per profile; deterministic. One place's description holds an injection sentence. A test-only switch (environment, off in compose) adds a delay or an oversize result.
  - `mcp-rag-ref`: the phase 04 pipeline over MCP, tools `register`, `status`, `search`, `fetch`, with scope fields always injected. It enforces scope itself, reads MinIO with a read-only user of its own (ADR-0178), never parses a document in its own process (ADR-0176), keeps its chunks in schema `rag_ref`, and applies no policy (ADR-0158).
  - Python, official SDK, each its own container on an internal network, never published to the host.
- Consequences: The owner's servers replace them by configuration and a contract test run.

### ADR-0212: MCPDocumentBackend over the platform MCP client; docs_search stays the guard point (I11)
- Date: 2026-10-15
- Phase: 05 (implementation choice I11)
- Status: decided
- Decision: `MCPDocumentBackend` implements `DocumentBackend` with the platform MCP client as transport (allowlist, auth, timeouts, size caps, audit). The rag server's tools are declared to no capability, so no model can call them around `docs_search`. `config/docs.yaml` `backend: internal | mcp`, overridable by `DOCS_BACKEND` in `.env`; `internal` stays the default.
- Consequences: The same contract suite and `rag.jsonl` run on both backends.

### ADR-0213: Declarative capabilities: extract, fixed steps, findings; no graph.py (I12)
- Date: 2026-10-15
- Phase: 05 (implementation choice I12)
- Status: decided
- Decision: A manifest section `declarative: {extract: {role: extractor, prompt, schema, on_missing: ask}, steps: [{id, tool, args}], findings: {prompt}}` replaces `graph.py`. A core runner (`capabilities/declarative.py`, capability-agnostic) builds the subgraph: one structured-output call fills `extract` (validated; one repair call on invalid output); steps run in order, their arguments mapped deterministically from `$.extract.<field>`, `$.steps.<id>.result.<path>` or literals; a missing extract field or an empty step result stops with a findings note (the answer asks the user, or lists up to three candidates). The model never fills a tool's arguments directly; the broker validates every call against the pinned schema. No expressions, conditionals, loops or parallelism (phase 07 may supersede this syntax). A capability with a `graph.py` keeps working unchanged.
- Consequences: A simple integration is a manifest, prompts and evals.

### ADR-0214: Tool progress labels in tool_start; API and events 2.2.0 (I13)
- Date: 2026-10-15
- Phase: 05 (implementation choice I13; ADR-0194 was 2.1.0)
- Status: decided
- Decision: `tool_start` carries `label {ar, en}` from `config/mcp.yaml` or the tool spec; the web shows the label in the user's locale, with a generic fallback string, so a new MCP capability needs no web change. Agent and gateway OpenAPI and the events schema move once, 2.1.0 → 2.2.0 (additive: the event label, the status warnings of ADR-0201); `make openapi` regenerates the web's types.
- Consequences: Changes until this PR merges stay within 2.2.0 (ADR-0046).

### ADR-0215: The reference routing capability is `directions` (I14)
- Date: 2026-10-15
- Phase: 05 (implementation choice I14; amends the CLAUDE.md phase 05 row)
- Status: decided
- Context: `run_evals` names a capability suite by its slug, and `routing` is the router suite.
- Decision: Slug `directions` (name ar «الاتجاهات والمسارات»). Permission `cap:directions` in the realm export, held by the seed users `analyst` and `evaluator`, not by `viewer` and not in the base `user` role. `min_tier: standard`, `ui.mode: auto`, `tools: [routing.geocode, routing.route]`, `context.needs: [window]`, `documents.collections: []`. Text answers only (distance, duration, main steps); maps wait for artifact outputs (phase 11). The CLAUDE.md phase 05 row names the slug.
- Consequences: The router suite keeps its name.

### ADR-0216: Routing rule keyword_candidate (I15)
- Date: 2026-10-15
- Phase: 05 (implementation choice I15; README §5.3; ADR-0145 precedent)
- Status: decided
- Decision: A generic manifest rule `keyword_candidate`: a keyword match makes the capability a router-model candidate and never selects it. `directions` declares it with Arabic, Gulf and English keywords. Turns with no candidate cost no router call, as today. The router cache key is unchanged (ADR-0169).
- Consequences: «ما الطريق إلى النجاح» reaches the router model, which sends it to general; no keyword alone decides a route.

### ADR-0217: External MCP servers need an env flag, never run under airgap, and need a PRE_TOOL policy per call (I16)
- Date: 2026-10-15
- Phase: 05 (implementation choice I16; ADR-0199)
- Status: decided
- Decision: A server with `internal: false` is enabled only when `.env` sets `MCP_ALLOW_EXTERNAL=true`; it is refused under the airgap overlay whatever `.env` says; and the broker refuses every call to its tools unless an enabled PRE_TOOL policy is scoped to the tool or its server. Proven with a test-only fixture config; `config/mcp.yaml` ships no external server.
- Consequences: Enabling an external server is an owner decision recorded as `.env`, config and policy changes together.

### ADR-0218: The official MCP SDK is pinned at 1.30.0
- Date: 2026-10-15
- Phase: 05 (I2 asks for an exact pin)
- Status: assumed
- Context: On 2026-10-15 PyPI held `mcp` 2.3.0 (2026-10-02) and the 1.x line's 1.30.0 (2026-09-07). 2.x is a new major with a different HTTP stack (`httpx2`); 1.30.0 depends on the `httpx` the agent already uses.
- Decision: `mcp==1.30.0` in services/agent/pyproject.toml and uv.lock, used for the client (`streamable_http_client`, `ClientSession`) and the reference servers (`FastMCP`). Moving to 2.x is a later, measured change.
- Consequences: The reference servers' input schemas, and so their pins in config/mcp.yaml, are what this SDK generates; `make mcp-hashes` shows any drift after an upgrade.

### ADR-0219: Every tool call reaches the audit record, content-free, through the terminal event's tool_audit
- Date: 2026-10-15
- Phase: 05 (G8; README §6.1 item 4 "tool calls summary")
- Status: assumed
- Context: The broker emitted spans per tool call but the gateway's audit record held no tool calls; G8 asks for audit records with server, tool, status, latency, sizes and an HMAC of the arguments, never their text.
- Decision: The broker reports one entry per call (`call_id, tool, caller, status, error code, latency_ms, retries` and, for an MCP tool, `server, request_bytes, response_bytes, args_hmac`) to the turn; the terminal `done` or `error` event carries them as `tool_audit` (agent to gateway only, like `guard_audit`). The gateway copies those fields, and only those, into the audit detail as `tool_calls`, and removes `tool_audit` before relaying the event. The `tool.exec` and `mcp.call` spans carry the same facts.
- Consequences: A tool call is auditable without its content. Events schema 2.2.0 gains the field (ADR-0214).

### ADR-0220: directions declares context.needs [window, findings]
- Date: 2026-10-15
- Phase: 05 (I14; ADR-0161)
- Status: assumed — Accepted on 2026-10-18
- Context: I14 fixes `context.needs [window]`. Under ADR-0161 the answer package holds only the blocks the turn's capabilities declare, and `findings` is one of them: with `[window]` alone, the answer would never see the route the capability found.
- Decision: The manifest declares `[window, findings]`: the window I14 names, plus the block that carries the capability's own result. The alternative, the core adding `findings` for every declarative capability, would be a core change outside I12.
- Consequences: The zero-core-change check holds. If the owner prefers the core rule, it is a one-line change in answer_needs with a superseding ADR.

### ADR-0221: The byte cap and the protocol's limits are enforced in an httpx transport under the SDK
- Date: 2026-10-15
- Phase: 05 (I2, I7, G7)
- Status: assumed
- Context: The SDK reads a response body whole before parsing it, and a body it fails to read (or a transport error) either leaves the call waiting for its timeout or ends the task group the pooled session lives in, failing every other call.
- Decision: `app/mcp/transport.py` wraps the SDK's httpx client. It reads each POST response itself: past `max_result_bytes` (a `tools/call`; protocol messages are bounded at 1 MiB) it stops reading and hands the SDK a JSON-RPC error for that request, `tool_result_too_large`; an SSE answer is read only up to the event that answers the request; responses must come without content encoding. A timeout or connection error is that request's JSON-RPC error (`tool_timeout`, `tool_unavailable`), never an exception inside the session. The server's GET stream (sampling, elicitation, roots) is answered 405, which the protocol allows. Each request's sizes are kept by a platform call id sent in `_meta`.
- Consequences: One slow or oversize answer never ends the session other calls share (tested: 20 calls to a slow server while another user's turn completes). The server sees an opaque call id in `_meta`.

### ADR-0222: A structured tool result keeps its shape through POST_TOOL
- Date: 2026-10-15
- Phase: 05 (I7; ADR-0165)
- Status: assumed
- Context: sanitize_untrusted works on text; run over a JSON string it can remove a quote with an instruction-like phrase or cut it at the size cap, so the declarative runner could not read the result any more.
- Decision: `ToolOutput` and `ToolResult` gain `data`, an MCP tool's structured content. POST_TOOL sanitizes and checks every string leaf of `data` together with `content` and the sources' readable fields, the several-texts-as-one mechanism of ADR-0165; keys and numbers stay as they are. `content` keeps the JSON text for the model.
- Consequences: The runner maps arguments from checked values. Keys of a structured result are not sanitized; the findings prompts render values only.

### ADR-0223: MCP tools are registered under the provider owner `mcp`, need no permission of their own, and report their caller
- Date: 2026-10-15
- Phase: 05 (I4, D2, D3)
- Status: assumed
- Context: A broker tool had an owning capability and a `required_permission`; an MCP tool belongs to no capability, and several capabilities may declare it.
- Decision: MCP tools register under the owner `mcp`, which is never a guard scope; `required_permission` may be None, so the caller's own permission and its manifest declaration decide (ADR-0160). `tool_start` and `tool_end` name the calling capability. Every allowlisted tool is registered whether or not its server is enabled here: a manifest stays valid in every profile, and a tool of a disabled server fails `tool_unavailable` before anything is sent.
- Consequences: Policies apply under `tool:{server}.{tool}`, `tool:{server}.*` and `tool:{caller}`.

### ADR-0224: A capability's backend may be overridden per environment; uploads record their backend
- Date: 2026-10-15
- Phase: 05 (I1, I11)
- Status: assumed
- Context: I11 makes `DOCS_BACKEND` override config/docs.yaml's `backend`; I1 says config/<slug>.yaml names the backend a capability uses. After a switch, files registered with the other backend must still report their status.
- Decision: The core reads `<SLUG>_BACKEND` from the environment over `backend` in config/<slug>.yaml (for config/docs.yaml, `DOCS_BACKEND`), and the uploads' backend follows the same rule. Migration 0013 adds `agent.attachments.backend`; status is always read from the backend an upload was registered with, and the ingest service indexes only the internal backend's uploads. A conversation's search uses the capability's current backend only, so files from before a switch are not searched after it.
- Consequences: `make eval` switches the agent to `mcp` and back without touching the ingest service.

### ADR-0225: mcp-rag-ref's own role, schema, MinIO user and sandbox
- Date: 2026-10-15
- Phase: 05 (I10)
- Status: assumed
- Context: I10 says the RAG reference server never parses a document in its own process, parsing staying in the ingest service's limited child processes, keeps its chunks in schema `rag_ref` and reads MinIO with a read-only user of its own.
- Decision: The server runs the same sandbox the ingest service runs (app/rag/ingest/sandbox.py: a spawned child per file with CPU, memory and time limits) in its own container (mem_limit 2g), rather than handing files to the ingest service, which would then write another service's schema. It connects as role `rag_ref`, which owns schema `rag_ref` in database `app` and has no privilege on schema `agent`; `postgres-roles`, a one-shot job, creates the role and schema on a new or existing volume. MinIO user `ragref-uploads` reads uploads only. Knowledge base documents are the tenant's (stored with an empty user id). The server needs `MCP_RAG_TOKEN` as a bearer token. Both reference servers are built from infra/mcp-ref on the agent's dependencies and shared libraries.
- Consequences: Two services parse documents, each in limited children. The core's `Database` and `PgVectorStore` take a schema and a table so a second service reuses them.

### ADR-0226: Declarative runner details
- Date: 2026-10-15
- Phase: 05 (I12)
- Status: assumed
- Decision:
  - The extract call is the capability's model call with its own prompt as the system block (`call_model(role, step, prompt, prompt_vars)`; only `{slug}.*` prompts). Its JSON is found in the reply and validated against the schema; one repair call says how many problems the first had. The tier is the resolver's (the capability floor applies).
  - `required` fields of the extract schema are the ones `on_missing: ask` checks; an empty optional field leaves its argument out, so the tool's default applies.
  - When a reference to an earlier step resolves to nothing, the findings carry up to three candidates from that step's `matches` or `candidates` list, and which step it was.
  - A failed step stops the run with its content-free code (`tool_unavailable`, `tool_timeout`, `policy`, ...); the findings prompt says so. A result that is not structured is read as JSON text, else `{text}`.
  - A declarative capability holds no Python file; the registry refuses one.
- Consequences: The capability's prompts decide every wording; the runner names no capability.

### ADR-0227: MCPDocumentBackend reports the rag.retrieve span; a status the server cannot give reads pending
- Date: 2026-10-15
- Phase: 05 (I11, G1, ADR-0137)
- Status: assumed
- Decision: `MCPDocumentBackend.search` records the same content-free `rag.retrieve` span as the internal backend (chunk ids, pages, section hashes, scores; its duration is the server round trip), so hit@8 and search latency are measured alike. When the server cannot answer a status, the file reads `pending` until it does. Its calls to the server are transport behind `docs_search`, which is the broker call and the audit entry; each has its `mcp.call` span.
- Consequences: rag.jsonl reports both backends side by side with the same metrics.

### ADR-0228: Web: a tool's label replaces the generic chip; the partly scanned badge uses the app's Arabic numbers
- Date: 2026-10-15
- Phase: 05 (I13, D7)
- Status: assumed
- Decision: A tool chip shows the `tool_start` label in the UI language, with the tool's name in the tooltip; a tool without a label keeps the generic chip ("أداة" and the name). The D7 badge reads «جاهز — N صفحات صور لم تُقرأ» with the Arabic plural (one, two, few, other) and a note under the file; its number is formatted like every other number of the app (`Intl.NumberFormat("ar")`), so the digits follow the runtime's Arabic default rather than always «٥».
- Consequences: A new MCP capability needs no web change.

### ADR-0229: Evals: rag@mcp, directions metrics, provider 429 backoff
- Date: 2026-10-15
- Phase: 05 (G1)
- Status: assumed
- Decision: `make eval` runs rag.jsonl twice, the second time as `rag@mcp` after recreating the agent with `DOCS_BACKEND=mcp`, and restores the internal backend in a `finally`. The directions suite scores four metrics per case kind against evals/thresholds.yaml; numbers are compared after folding Arabic-Indic digits and decimal marks. A turn that ends `model_unavailable`, or a judge call refused with 429, is retried after 5, 10, 20, 40 and 60 s; each suite records its retries. A routing case without a document checks the turn's capabilities.
- Consequences: A run survives a short provider limit; a longer one still fails the case.

### ADR-0230: Transport operations are their own class, called only through broker.transport (supersedes ADR-0200)
- Date: 2026-10-18
- Phase: 05 (fix session on PR #10, owner decisions 1 and 3; owner decision D6 stands as written)
- Status: decided
- Context: Review of PR #10: MCPDocumentBackend held a `ServerConnection` and called it directly, outside the broker (rule 6, D3, ADR-0197), and ADR-0200 called the rag server's `register` read-class although it writes on the server and starts indexing.
- Decision:
  - D6 stands as the owner wrote it: model-callable MCP tools are read only in phase 05. `config/mcp.yaml` entries gain `class: tool | transport` (default `tool`). The rag server's operations are `class: transport`; `register` is marked `risk: write`, the others `risk: read`. A `class: tool` entry with `risk: write` is still refused; no entry of either class may delete.
  - A transport operation is never a broker tool: it is not registered, a manifest that declares one stops the agent at startup, and it is reached only by a backend adapter through `broker.transport(server, tool, args, scope)`.
  - `broker.transport` applies what a tool call gets: the allowlist (`class: transport` entries only), the pinned schema without the injected fields (a call naming one is refused before anything is sent), the injected fields from the scope the core built, the server's auth, timeout, byte cap and breaker; and it writes the same content-free `tool.exec` span and audit entry (server, tool, status, latency, byte sizes, argument HMAC). Inside a turn the entry joins the turn's tool audit under the capability it serves (a context the broker sets around a tool's handler and the core around its own document reads: evidence, statuses); outside one (an upload's registration, the file list) it is written to the `audit.transport` log, caller `transport`.
  - MCPDocumentBackend's register, status, search and fetch, the evidence fetch of graph/evidence.py included, go through it. The broker holds the MCP client: no module outside app/mcp and the broker imports the client or reaches a connection (a lint test refuses it). An evidence fetch that fails leaves those sources without text, by its code, instead of ending the turn.
- Consequences: Every call to an MCP server, tool or transport, is guarded, bounded and audited in one place. The audit's `caller` is a capability slug, `attachments` (status reads in a turn) or `transport` (outside a turn).

### ADR-0231: No capability reaches a backend or a connection; tools get a scope-bound document facade
- Date: 2026-10-18
- Phase: 05 (fix session, owner decision 2; amends ADR-0202 on `RuntimeContext.documents`)
- Status: decided
- Decision: `RuntimeContext` carries no backend: `documents` is removed. The broker resolves a tool's backend from the calling capability's `config/<slug>.yaml` and hands the handler, in `ToolContext.documents`, a `DocumentFacade` (app/rag/access.py) whose only fields are three functions, `search`, `fetch` and `status`, bound to the scope the broker built; no field holds a backend, a transport or a connection. A capability whose manifest declares no `documents.collections` gets none. The capability's broker is likewise a function bound to the turn, not an object holding the turn context. Tests walk every attribute, field, mapping and bound method reachable from a RuntimeContext (directions, general) and a ToolContext and find no backend, broker, client, connection or Services.
- Consequences: What remains reachable is a closure's cells, the in-process reflection limit ADR-0053 names. "docs reaches only its configured backend" is read with ADR-0232: its configured backend, and the backend that indexed each of its files.

### ADR-0232: A file is searched on the backend that indexed it
- Date: 2026-10-18
- Phase: 05 (fix session, owner decision 11; supersedes the last sentence of ADR-0224's decision)
- Status: decided
- Decision: Each attachment record keeps its backend (migration 0013). The facade groups the scope's session collections by the backends their files were registered with (the configured backend when a conversation holds none), searches each group on its backend at once, and merges the hits by score, best first; a knowledge base collection goes to the configured backend. A backend that fails is left out when another answered. `status` uses the record's backend. Switching `DOCS_BACKEND` changes new uploads only. Tested: a file indexed internally before a switch and one on the new backend after it are both searched and cited.
- Consequences: Both backends rank with the same fusion of the same pipeline, so scores merge; another backend would need comparable scores or a merge of its own.

### ADR-0233: Status reads never stall a turn: one bound, the last known status
- Date: 2026-10-18
- Phase: 05 (fix session, owner decision 10; amends ADR-0227 on a status the server cannot give)
- Status: decided — Superseded by ADR-0245 on 2026-10-19 (a read past the bound is not cancelled)
- Decision: A conversation's attachment statuses are read at once, through `broker.transport` for an MCP backend, under one bound for all of them: `status_timeout_s: 2` in config/docs.yaml. A read past the bound is cancelled; a read that fails (a server down, a breaker open) raises; either way the record's last known status is shown. An MCP backend's status, when read, is kept on the record (`AttachmentStore.remember_status`, only for records of a backend other than internal, whose records are their status already). A record never read reads `pending`, as ADR-0227 had it. Tested: with a rag server whose status hangs, a general turn in a conversation holding three of its files completes within the bound of its normal time, and the files show `indexed`, their last known status.
- Consequences: A turn with files on a hung backend costs at most 2 s more; the file list may show a status one read old.

### ADR-0234: Nothing from a server or an exception leaves as text; structured keys are checked like values
- Date: 2026-10-18
- Phase: 05 (fix session, owner decision 12, G8; supersedes ADR-0222 on keys)
- Status: decided
- Decision:
  - The `mcp`, `httpx` and `httpcore` loggers (children included) log WARNING and above, and every record they create is scrubbed where it is made (the log record factory, app/observability/logs.py): its arguments, exception and stack are dropped, and a message without %-style arguments, which may be an f-string around a server's text, is withheld whole; the logger, module and line remain.
  - `tool_calls.error`, span errors and audit entries carry a code only: `invalid_arguments`, `tool_timeout`, `tool_failed` (any other handler exception), or the ToolFailure code; never an exception's or a server's text.
  - A structured result's keys are readable text: POST_TOOL sanitizes and checks each key with the values (the several-texts-as-one mechanism of ADR-0165). A key a check changes is renamed; one left empty, or whose new name is taken, drops its entry. Tested with a national ID as a key: redacted for a user, kept for the holder of a role exception (ADR-0107).
  - The directions findings template renders `distance_km`, `duration_min` and each step's `distance_km` only when they are numbers (a boolean is not); a missing number becomes a note telling the answer to give none; every rendered value is escaped (`directions.findings` 1.2.0).
- Consequences: Library diagnostics lose their detail below WARNING and their message when it is not a template; the code path stays in the record. Tested: a server exception and a malformed JSON answer leave no server text in logs, spans, audit entries or the tool call's error.

### ADR-0235: No docs-named setting in the core; uploads go to the backend the session readers name
- Date: 2026-10-18
- Phase: 05 (fix session, owner decision 5; amends ADR-0224 on where the override is read)
- Status: decided
- Decision: `docs_backend` is removed from app/settings.py and app/rag/setup.py. The only override of a backend is the generic `<SLUG>_BACKEND` of app/capabilities/registry.py over `config/<slug>.yaml` `backend`. New uploads go to the backend named by the capabilities whose manifest reads session collections (`internal` when none names one); two such capabilities naming different backends stop startup. `build_documents` takes that name from its caller.
- Consequences: `DOCS_BACKEND=mcp` still switches uploads and docs together, through the docs capability's configuration, and the core names neither.

### ADR-0236: The MCP transport reads incrementally, counts every byte and ends each request at a wall-clock deadline
- Date: 2026-10-18
- Phase: 05 (fix session, owner decision 8, G7; amends ADR-0221)
- Status: decided — Superseded by ADR-0246 on 2026-10-19 (the shared session; server requests dropped)
- Decision: app/mcp/transport.py passes an SSE response to the SDK one complete event at a time, through an incremental parser: each byte is scanned once and only the event being received is buffered. Every byte of the response, notifications and the server's own requests included, counts toward `max_result_bytes` (a `tools/call`; 1 MiB for the protocol's messages); past it the connection is closed and the request ends `tool_result_too_large`. `timeout_s` is a wall-clock deadline from send to the last byte: at the deadline the read is cancelled, the connection closed and the request ends `tool_timeout`. A stream that ends without its answer ends the request `tool_unavailable`. A server's requests on the call's stream reach the SDK, which refuses sampling, elicitation and roots, and the call goes on. Tests, with the misbehaving servers in a thread of their own: 1 MiB of progress notifications before the result, and one byte every 2 s; in both the agent's event-loop lag stays under 100 ms (a probe task), another user's call on the same server and a general turn take their normal time, and the trickling connection is closed when the call ends.
- Consequences: The SDK sees a request's events as they arrive, so a long-running call can stream progress later (phase 11) without a change here.

### ADR-0237: The breaker's trial resolves in a finally; a full queue is tool_busy, not a server failure
- Date: 2026-10-18
- Phase: 05 (fix session, owner decision 9; amends ADR-0209)
- Status: decided — Superseded by ADR-0244 on 2026-10-19 (a cancelled trial)
- Decision: The half-open trial is resolved in a `finally`: a success closes the breaker; any other end, a cancellation or a ToolFailure included, reopens it. In the closed state an answer the server gave (a tool error, an answer over the cap) still counts as the server up. Waiting for the per-server semaphore past `timeout_s` fails `tool_busy` and leaves the breaker as it was; the send then has its own `timeout_s`, so the broker's bound on an MCP call is twice the server's timeout plus its margin. `_reset` ends a session only if it is still the current one and clears the connected flag at once, so a call re-sent after a re-initialisation waits for the new session (I9). Tested: a cancelled, a failed and a refused trial each reopen it and a successful one after each closes it; 50 waits on a full queue are `tool_busy` and leave it closed with no failure counted; the first call after a server restart succeeds after one re-initialisation, with no retry in the test.
- Consequences: A burst beyond `max_concurrency` never opens a breaker; only the server's own failures do.

### ADR-0238: A declarative step declares its result list and when it is empty or ambiguous
- Date: 2026-10-18
- Phase: 05 (fix session, owner decision 6; amends ADR-0226 on candidates)
- Status: decided
- Decision: The runner names no result shape. A step may declare `result_list: $.<path>`, `empty_when: {fewer_than: N}` (default 1) and `ambiguous_when: {more_than: N}`. A list with fewer items than `empty_when` stops the run after that step, status `empty`. A later reference into the step that resolves to nothing stops the run `ambiguous` when the list holds more items than `ambiguous_when`, `unresolved` otherwise; up to three items are the candidates. directions declares `result_list: $.matches, ambiguous_when: {more_than: 1}` on both geocode steps; its findings template says "not found" for `empty`, lists the choices for `ambiguous`, and asks about the one close match for `unresolved` with a candidate. Tested with the test-only declarative fixture `junction`, whose list sits under `options.items`.
- Consequences: A geocode answer with one weak match and no best was "choose one of these" and is now "did you mean"; an unknown place stops at its own geocode step instead of the next one.

### ADR-0239: The core delimits a declarative capability's step results
- Date: 2026-10-18
- Phase: 05 (fix session, owner decision 7)
- Status: decided
- Decision: After the capability's findings template, the runner appends each step's result, POST_TOOL-checked, as JSON inside the untrusted delimiters rendered by the core's `context.tool_result` prompt (tool name escaped), whatever the template says. The registry hands the runner the core's prompt library. Tested with a fixture whose template has no delimiters.
- Consequences: The answer sees a step's data twice when the template also renders it (directions does, delimited and escaped); the cost is the size of the results.

### ADR-0240: Capability evals declare their metrics with generic scorers; the harness names no capability
- Date: 2026-10-18
- Phase: 05 (fix session, owner decision 4; amends ADR-0229 on the directions metrics)
- Status: decided
- Decision: evals/run_evals.py names no capability: `DIRECTIONS_METRICS` and the default `["directions"]` are removed. A suite's folder may hold `metrics.yaml`: `case_kind` (the case field that selects metrics), `common` checks, and per metric the checks per kind, with the scorers `field_equals`, `number` (within a tolerance, Arabic-Indic digits and decimal marks folded), `candidates` (at least N of the expected items, or all), `abstention` (whole phrases) and `text_absent` (expected texts or patterns). Suites stay globbed from `capabilities/*/evals`. capabilities/directions/evals/metrics.yaml declares the four metrics. Rescoring the recorded answers of runs make-eval-20261015T122036Z and make-eval-20261015T130421Z with the old code and with the new gives the same scores and no per-case difference.
- Consequences: A capability's evals are its folder only (zero core change).

### ADR-0241: Routing evals, honestly: keyword negatives, keyword-free coverage, the geocoder change and run 122036Z
- Date: 2026-10-18
- Phase: 05 (fix session, owner decision 13)
- Status: decided — Accepted on 2026-10-18
- Context: After run make-eval-20261015T122036Z the directions keyword list was extended (walking: «أمشي», «امشي», «مشيًا», «مشيا», "walk from", "walking"; travel time: «كم ياخذ», «كم يستغرق»; "how do i get", "get to"; commit 5c27568) and no ADR recorded it; in the same commit the reference geocoder was changed to match partial names on Arabic stems.
- Decision:
  - The extended keywords stay, accepted by the owner on 2026-10-18.
  - Recorded: the reference geocoder was changed after run 122036Z so that «الجامعة» (and «الحديقة») match several places on their stems and are ambiguous, not unknown; and the four directions cases of that run that went to general pass only through keywords added after that run, not through anything the router model learned.
  - routing.jsonl gains ten keyword negatives labelled general, gated as before (Arabic, Gulf, English: «كم ياخذ إصدار التصريح؟», "How do I get a refund…", "Please get to the point…", «ما المسافة بين الأرض والقمر؟», "How far is the Moon from Earth?", …), and nine directions questions holding none of the keywords («أبي أروح للمطار», "Take me from Kingdom Centre to the airport", …) as the set `keyword_free`: a case with `report_set` is scored and reported under its set and never counts toward the suite's rate (run_evals.py), so the set measures what the keyword rule misses.
- Consequences: The routing rate is gated on cases that test the router's judgment, and the keyword rule's blind spot is a reported number.

### ADR-0242: Least privilege for the reference servers: the routing server's own network, CONNECT kept from PUBLIC
- Date: 2026-10-18
- Phase: 05 (fix session, owner decision 16)
- Status: decided
- Decision: mcp-routing-ref joins only `mcp-routing`, an internal network it shares with the agent (no postgres, minio or litellm), in compose and in the airgap overlay; airgap_check and smoke assert it. CONNECT is revoked from PUBLIC on the business, keycloak, langfuse and agent_test databases and granted back to each one's owner, the role that uses it (infra/postgres/init on a new volume, infra/postgres/roles/connect.sh on every `make up` and `make test-agent`). rag_ref connects only to its own: `app`, where its schema is, and `rag_ref_test`, a database of its own (CONNECT for rag_ref only, schema `rag_ref` owned by it, no CREATE on `public`) on which `make test` runs the reference RAG server as that role, so the tests exercise the role separation. Tested: rag_ref cannot connect to business, keycloak, langfuse or agent_test.
- Consequences: The reference RAG server under test no longer shares the agent's test database. `mcp-rag-ref` still receives `LITELLM_MASTER_KEY` (docs/BACKLOG.md, phase 12).

### ADR-0243: The phase 04 checkpoint fixture and the strengthened tests
- Date: 2026-10-18
- Phase: 05 (fix session, owner decision 14)
- Status: assumed
- Decision: tests/fixtures/checkpoints/phase04_docs_conversation.json holds the checkpoint rows (36 checkpoints, 62 blobs, 142 writes) of one conversation, a docs turn and a general turn, run by the phase 04 code at 30302d2 (the merge of PR #4) with the generator beside it; LangGraph versions were the same then and now. The G9 test loads those rows and reopens the conversation through the history endpoint: its questions and answers come back unchanged; phase 04 kept a turn's citations in the per-turn `citations` channel, not on the reply, so the replies carry none (ADR-0193 shows citations when they were recorded), and the recorded ones still load, without backend or collection. The other tests the review named: the injection sentence itself must sit inside the delimiters and the answer must equal a route's without it and hold nothing it asked for (make test's answer model is the mock; the eval's injection cases test real models); a docs turn's reply must record a citation before its shape is checked (the turn's answer quotes the file); the reference RAG server runs as rag_ref (ADR-0242); a fixture server sends sampling, elicitation and roots requests on the call's stream and all three are refused while the call succeeds.
- Consequences: A change to the checkpoint format or the history's reading of it fails a test against data the current code did not write.

### ADR-0244: A caller's cancellation never decides the breaker
- Date: 2026-10-19
- Phase: 05 (narrow fix session on PR #10, review round 2, C1, owner decision 1; supersedes the cancellation part of ADR-0237)
- Status: decided
- Context: Review round 2: a status read's 2 s bound cancelled the breaker's half-open trial, and ADR-0237 reopened the breaker on any end but a success, a cancellation included. A server answering in 2.5 s within its 10 s timeout was kept open for another 30 s by one user's file list, and every other user's calls to it were refused.
- Decision: A call cancelled by its caller (a status bound, a capability time limit, a client disconnect) releases the half-open trial without a verdict (`Breaker.release`): the breaker stays half open and the next call may be the trial. Only the server's own outcome decides: a success closes it; a server error or the call's own `timeout_s` reopens it (the rest of ADR-0237 stands: an answer past the cap, a ToolFailure, still reopens a trial; `tool_busy` leaves the breaker as it was). Tested (tests/integration/test_mcp_sessions.py): the reviewer's case with a server answering in 2.5 s, timeout 10 s, the breaker half open: user A's file list returns at its 2 s bound and the breaker is not reopened; A's read, no longer cancelled (ADR-0245), ends at 2.5 s and closes it, and user B's next call reaches the server; the same trial cancelled by its caller at 2 s is released and B's next call is the trial, reaches the server and closes it; a trial cancelled ten times in a row never reopens and never wedges; a trial past the server's `timeout_s` still reopens.
- Consequences: A server is judged only by what it did. One trial at a time stays (ADR-0209): a call that arrives while a trial is still running, such as A's read between 2 s and 2.5 s, is refused `tool_unavailable` as before.

### ADR-0245: A status read past its bound goes on in the background; one read per attachment
- Date: 2026-10-19
- Phase: 05 (narrow fix session on PR #10, review round 2, C1, owner decision 2; supersedes ADR-0233 on cancelling a read past the bound)
- Status: decided
- Context: Review round 2: a status read cancelled at its 2 s bound left its HTTP request running inside the SDK until the server's `timeout_s`, outside the per-server semaphore, so one user polling a list of files against a slow server filled the HTTP pool and other users' calls waited.
- Decision: When the bound (`status_timeout_s`, 2 s) passes, the turn or the list shows the record's last known status, as ADR-0233 has it, and the read is not cancelled: it ends in the background, within its server's semaphore and `timeout_s` (it is an ordinary `broker.transport` call), and its result is kept on the record (`remember_status`). At most one read per attachment is in flight (AttachmentService keeps them by attachment id); a later list read, or a read of the one attachment, joins it. A read belongs to no caller: a caller that stops waiting, by its bound or its own cancellation, leaves it running. Tested: with a server answering status in 5 s, one user reading a 10-file list 20 times (two tabs, ten rounds) holds at most 10 requests at the server, each list returns within its bound, the reads' results update the cached statuses, and another user's general turn completes in its normal time.
- Consequences: A read's audit entry goes where the read was started (the turn that started it, or the transport log); a turn that joins a read records none of its own. A slow backend's status reaches the list one poll later, as before.

### ADR-0246: One MCP session per call in flight; server requests dropped unanswered
- Date: 2026-10-19
- Phase: 05 (narrow fix session on PR #10, review round 2, C2, owner decision 3; supersedes the shared session of ADR-0210 and ADR-0236 and the refusal of ADR-0203; I9 stands)
- Status: decided
- Context: Review round 2: every call to a server shared one MCP session. The SDK answers a server's request (sampling, elicitation, roots) with a POST it sends inline, from the one task that sends all of the session's messages, so a server holding that POST stalled every other user's call to it; and a call cancelled by its caller left its HTTP request open at the server, outside the semaphore.
- Decision:
  - Each server has a pool of up to `max_concurrency` sessions, each used by one call at a time. A call takes a session with its semaphore slot and returns both when its HTTP request has ended. A session lives in a task of its own (the SDK's contexts must be entered and left in one task); a call that ends with the server's answer (a result or a tool error) puts its session back in the pool.
  - A call that is cancelled, times out or fails any other way closes its session: leaving the SDK's contexts cancels the request still open on it, which closes its HTTP connection, then the session is ended with a DELETE (bounded by `timeout_s`; the SDK's own DELETE, sent while requests still run, is switched off). Only then, after a fresh session has been opened in its place, is the slot returned, so requests open at a server never exceed `max_concurrency`.
  - The first session of a generation pins the tools (I5). A call that finds the server unreachable, or not knowing its session (I9: the server restarted), ends the generation: the connection task opens and pins a new one, and in the second case the call is sent once more on it.
  - Server-initiated requests (sampling, elicitation, roots) are dropped unanswered by the transport, on an SSE stream or as a JSON body (the latter ends the call's request `tool_unavailable`, since its answer never came), and logged with the code `server_request_dropped` only; their bytes still count toward the cap. The call stays bound by its own deadline.
  - I9 stands: sessions carry no per-user state; every call carries all it needs.
- Tested (tests/integration/test_mcp_sessions.py, a server in a thread of its own that counts the requests open at it): the reviewer's case, a server that sends a sampling request on user A's stream and holds A's answer 3 s: user B's quick call on the same server takes under 0.5 s, and nothing is answered; twelve calls in three waves on a server with `max_concurrency` 4, each holding the server 5 s and cancelled after 0.3 s (or cut by a 1 s `timeout_s`): requests open at the server never exceed 4 and none is left open once the calls have ended; the item 14 test now asserts sampling, elicitation and roots all go unanswered, are logged as dropped three times, and the call succeeds.
- Consequences: A server may hold up to `max_concurrency` sessions; a cancelled or timed-out call costs one session's re-initialisation. A server that needs sampling, elicitation or roots to answer a call fails that call by its own deadline.

### ADR-0247: Keyword candidacy stays for directions; routing for all capabilities is decided in phase 08
- Date: 2026-10-19
- Phase: 05 (owner decision of 2026-10-18, recorded in the narrow fix session on PR #10)
- Status: decided
- Context: Run make-eval-20261018T141208Z and run make-eval-20261018T150216Z measured the keyword-free directions set (ADR-0241) at 0/9: without a keyword, the router model sends every such question to general. Making the router model decide on every turn would raise that number and add the router's latency to every turn.
- Decision: The `keyword_candidate` rule (ADR-0216) stays for directions as it is. Whether routing for all capabilities moves to the router model on every turn, or keeps keyword candidacy, is decided in phase 08 (kb), with the router's latency measured and the keyword-free coverage set (0/9 now) as the measure of what the keyword rule misses. docs/BACKLOG.md carries the item under phase 08.
- Consequences: Directions questions that hold none of the keywords keep going to general until phase 08.

### ADR-0248: The timeout gaps are fixed before phase 06, in a standalone infrastructure PR (D1)
- Date: 2026-10-21
- Phase: standalone PR before phase 06 (`fix/timeout-budget`, owner decision D1)
- Status: decided
- Context: The verification of PR #10 left the breaker suspicion and six timeout budget findings in docs/BACKLOG.md "Before the phase 06 build", with a recommendation pending the owner's choice: fix them inside phase 06 or before it.
- Decision: The timeout gaps are fixed before phase 06, in this standalone infrastructure PR, not inside phase 06. Scope: docs/BACKLOG.md "Before the phase 06 build" (the breaker suspicion and the six findings), nothing else (T1). Verified by `make test` and `make smoke` only (CLAUDE.md rule 13: infrastructure only, no `make eval`). The session prompt is kept verbatim in docs/TIMEOUT-BUDGET-PROMPT.md.
- Consequences: Phase 06 starts on a platform whose every covered bound is shorter than the one around it, checked by scripts/check_timeout_budget.py in `make test`. Rows of the timeout table this scope does not cover stay listed for phase 12.

### ADR-0249: A wait for the next generation never decides the breaker or ends a generation (scope A, reproduced)
- Date: 2026-10-21
- Phase: standalone PR before phase 06 (`fix/timeout-budget`, scope A; amends ADR-0244 and ADR-0246)
- Status: decided
- Context: The verifier of PR #10 suspected, from the code only, that the generation-reset path of app/mcp/client.py could count breaker failures against a healthy server. Reproduced before any change (T2) with tests/integration/test_mcp_generation_reset.py and a fixture server with real sessions (tests/fixtures/slow_mcp.py, `RestartingMCPServer`: every session forgotten on `restart()`, 404 on a forgotten one, nothing goes down). Scenarios, 50 iterations each with seeded jitter on when each call starts, how long the server holds it and when the restart lands: 1, 4 and 8 (max_concurrency) calls in flight; 16 calls, half of them queued for a slot; 8 calls whose sessions are being initialised when the restart lands. On main's code the first four passed and the queued scenario failed: at iteration 49 (and at iteration 1 of a probed rerun) four breaker failures were counted against the healthy server, one short of opening it for 30 s. A failure is counted by wrapping `Breaker.failure`, since a later success resets the count and the count left at the end of an iteration would hide it. Path: call A's session is gone (I9), A ends the generation and clears `connected`; call B, queued on the semaphore, gets its slot, finds no idle session and no connection, and `_take` raised `_Unreachable`; the attempt then counted a server failure and called `_reset` on the current generation, which by then was the next one being opened, so the connection task ended that one too as soon as it had pinned it.
- Decision: Only an attempt's own server outcome decides the breaker. `_take` finding no connected generation raises `_NoGeneration`, never `_Unreachable`: the call fails `tool_unavailable` with no verdict (`_Failed.server_failed` None), the breaker is left as it was (a trial is released, as a cancellation is, ADR-0244) and no generation is reset. The I9 re-send's wait for the next generation, if the attempt's deadline ends it, is also no verdict. A session that fails to open still resets its own generation only and still counts (the server was reached, or should have been, and failed). The test stays, with all five scenarios.
- Consequences: After the fix, 250 iterations (and the two failing scenarios twice more, 200 iterations) count no breaker failure and leave it closed. Not fixed here (T1): calls that arrive or get their slot while a healthy server's generation is being re-opened still fail `tool_unavailable` at once rather than waiting the few milliseconds for the next one (in the runs: 0 of 50, 19 of 200, 41 of 400, 93 of 800 and 139 of 400 calls, by scenario); docs/BACKLOG.md phase 12.

### ADR-0250: One deadline helper on anyio cancel scopes (I1)
- Date: 2026-10-21
- Phase: standalone PR before phase 06 (`fix/timeout-budget`, implementation choice I1)
- Status: decided
- Context: CLAUDE.md rule 12c asks for one deadline per call, from send to last byte, with every inner bound shorter than the one enclosing it. The timeout budget table of docs/RESILIENCE.md showed inner bounds set as fixed numbers, unaware of the bound around them (model calls 300 s inside capability limits of 30–60 s, embeddings 300 s inside docs_search's 45 s).
- Decision: Every bound is opened through services/agent/app/resilience/deadline.py: `within(ceiling_s, margin_s)` is an anyio cancel scope ending at min(now + ceiling, enclosing deadline − margin) and raises `DeadlineExceeded` (a TimeoutError) saying whether the call's own ceiling or the enclosing deadline ended it; `bounded(ceiling_s, margin_s)` is the same arithmetic for a library that takes a timeout of its own; the enclosing deadline is `anyio.current_effective_deadline()`. Alternatives rejected: `asyncio.timeout` (no lookup of the effective deadline across nesting: each layer would have to be handed the outer one by hand); a custom context-variable stack of deadlines (custom code where a maintained library fits, rule 12i). anyio is already in the image (a dependency of httpx, the MCP SDK and Starlette) and is now declared directly (`anyio>=4.4`).
- Consequences: An inner bound cannot be configured past its outer one: the arithmetic takes the minimum at run time. A row in docs/RESILIENCE.md "What we built". Tests: tests/unit/test_deadline.py.

### ADR-0251: The deadline crosses a task boundary in one context variable
- Date: 2026-10-21
- Phase: standalone PR before phase 06 (`fix/timeout-budget`; fills a gap in ADR-0250)
- Status: assumed — Accepted on 2026-10-21
- Context: anyio's cancel scopes belong to the task that opened them. A task created with `asyncio.create_task` reads `anyio.current_effective_deadline()` as infinite (measured on anyio 4.15.1, also with a copied context). LangGraph runs every node in a task of its own and the capability firewall runs each capability in an isolated task (ADR-0053), so a turn deadline opened by the runner would be invisible to every model and tool call below it.
- Decision: The end of the innermost scope opened by `within` also travels in one context variable, which a task copies at creation; the enclosing deadline is the earlier of anyio's and that variable. It is one value, set and reset by `within` only, not a stack of its own, and anyio still enforces every bound (each inner call opens its own scope from the inherited end). Rejected: handing the deadline through every call signature (TurnContext, the capability runtime, the broker, the model gateway; a core change across every layer and easy to miss in a new one); anyio task groups in place of LangGraph's tasks (not ours to change).
- Consequences: A task created inside a scope keeps its end even after the scope that created it has closed; a task created outside any scope (a background status read, ADR-0245; a background registration of I5) is bound only by its own ceiling, as work that belongs to no caller should be. The owner may prefer another carrier; this is the smallest that keeps anyio as the mechanism.

### ADR-0252: Sandbox children sized from measurements; each document service checks its memory budget at startup (I7)
- Date: 2026-10-21
- Phase: standalone PR before phase 06 (`fix/timeout-budget`, implementation choice I7, scope B)
- Status: decided
- Context: docs/BACKLOG.md, High: ingest and mcp-rag-ref each allowed two sandbox children of 1 GiB (RLIMIT_AS) in a 2 GiB container, so two hostile files at once could get the container OOM-killed and stop document reading for every user (CLAUDE.md rule 12).
- Decision: Measured with scripts/measure_sandbox.py: one sandbox child on the phase 04 corpus (evals/docs, nine files) peaks at 137.6 MiB resident and 157.2 MiB of address space (internal-policies.docx); the service's own process, with two files parsed at once (twenty-pages.pdf and leave-regulation.pdf, three rounds), peaks at 202.1 MiB (ingest) and 197.6 MiB (mcp-rag-ref). 2 × 137.6 MiB ≤ 640 MiB, so the child limit is 640 MiB (`ingest.sandbox.memory_mb`) and `mem_limit` stays 2g; the reserve is max(1.5 × 202.1, 512) = 512 MiB (`ingest.sandbox.parent_reserve_mb`, new). At startup each service reads its cgroup's memory.max (v2, then v1) and refuses to start, logging the numbers and what to change, when workers × memory_mb + parent_reserve_mb exceeds it (`check_memory_budget` in app/rag/ingest/sandbox.py; ingest uses `ingest.workers`, mcp-rag-ref its WORKERS): 2 × 640 + 512 = 1792 MiB ≤ 2048 MiB. Tests: unit tests of the check (the shipped values fit; the old 1024 MiB are refused at 2560 MiB; no limit is no refusal); a smoke step per service (two users' files that allocate up to the child's limit at once both end failed `extraction_aborted`, a third user's file uploaded meanwhile is indexed, the container is not OOM-killed and its restart count is unchanged) and a final smoke check that neither was OOM-killed during the run.
- Consequences: Reproduced on main's code before the change, with swap off (ADR-0253): the same two files got the ingest container `OOMKilled=true` (memory.events `oom_kill 1`), one child killed by the kernel. With 640 MiB children the cgroup peaked at 1364 MiB (ingest) and 1368 MiB (mcp-rag-ref) with no `max` event. A parser that needs more than 640 MiB of address space for a legitimate file now ends `extraction_aborted`; none of the corpus comes near it (157 MiB). The Docker Desktop figure is unchanged: 12 GB (README §21.1), each document service keeping 2 GiB.

### ADR-0253: The memory test's details: no swap, the existing failure code, a smoke step with a test-only entry module
- Date: 2026-10-21
- Phase: standalone PR before phase 06 (`fix/timeout-budget`, scope B; fills gaps in I7)
- Status: assumed — Accepted on 2026-10-21
- Context: Three details the session prompt left open. (1) Docker gives a container as much swap as its `mem_limit` unless told otherwise: on main's code two children of 1 GiB took the ingest cgroup to its 2 GiB limit 262 times and swapped instead of being OOM-killed, so the finding showed only as thrashing in development, and would be an OOM kill on a host without swap. (2) The prompt's tests expect both jobs to "fail resource_limit"; the platform's code for a child stopped by its CPU, memory or time limit is `extraction_aborted`, with Arabic and English messages already in the web. (3) The test needs the real containers (State.OOMKilled, the restart count) and real uploads, with a parser that exists only in tests.
- Decision: (1) `memswap_limit: 2g`, equal to `mem_limit`, on ingest and mcp-rag-ref: no swap, so the limit is the one `check_memory_budget` reads. (2) "resource_limit" is read as the existing `extraction_aborted`; no new code, message or API change. (3) The test is a smoke step: docker-compose.memtest.yml, layered over the stack for that step only, starts each service from services/agent/tests/fixtures/memtest.py, which wraps the sandbox's extractor when imported; spawn children import the main module again, so they parse with it too. No hook is added to platform code.
- Consequences: The owner may prefer a dedicated failure code; it would be a web string, an OpenAPI enum value and an API version bump. The smoke step recreates ingest and mcp-rag-ref (and the agent on DOCS_BACKEND=mcp, as phase 05's step does), restored by the EXIT trap on failure.

### ADR-0254: An upload answers within register_response_s; the registration goes on in the background (I5)
- Date: 2026-10-21
- Phase: standalone PR before phase 06 (`fix/timeout-budget`, implementation choice I5, scope C)
- Status: decided
- Context: docs/BACKLOG.md, High: an MCP upload registration could take up to 62 s (a full semaphore, then the call) against the gateway's 60 s on the agent call, so the user saw a failed upload while the file was registered behind it.
- Decision: config/docs.yaml `register_response_s: 20`. `AttachmentService.register` stores the record, then hands the file to the backend as work of its own, at most one per attachment id (a second request for the same id joins it), in a context without the caller's deadline (`deadline.detached_context()`, ADR-0251); it waits up to `register_response_s`. Past it the upload answers with the file `pending` and the hand-over goes on in the background, within the backend's own bounds (an MCP server's semaphore and `timeout_s`, an ordinary `broker.transport` call). Its outcome is kept on the record: a failure ends the file `failed` with the new reason `register_failed` (`AttachmentStore.mark_register_failed`), final: it holds over a status the backend reported meanwhile, `remember_status` never overwrites it, and the backend is not asked for that file's status again. The document facade drops from every search the chunks of a file whose registration failed, since a backend may have indexed the file and then failed the call, and reports its status as failed. The reference RAG server's `register` was already idempotent on the document id. The ADR-0245 status reads now also start in a detached context, so a turn's deadline never cuts a read it does not own (I2). Messages: `attachments.reasons.register_failed` in the web's ar.json and en.json; the reason is a free string in the API documents, so neither OpenAPI document changes and the API version stays 2.2.0 (T8).
- Consequences: Tested (tests/integration/test_upload_registration.py, the reference RAG server in a thread of its own): a server holding `register` 25 s → the upload answered `pending` in 20.00 s (bound 20.5 s), another user's upload meanwhile in 0.03 s (0.01 s alone), event-loop lag at worst 54.3 ms, and the file then ended indexed; a server that indexed the file and failed the call → the upload answered `failed` `register_failed` in 0.01 s, the server returned the file's chunks when asked directly and the platform's search never did, and a later `remember_status` left the record failed. A registration still in the background when the agent stops leaves the file `pending` (no new behaviour; the phase 12 backlog keeps the per-job deadline).

### ADR-0255: One deadline per turn, with a reserve for the answer (I2)
- Date: 2026-10-21
- Phase: standalone PR before phase 06 (`fix/timeout-budget`, implementation choice I2, scope D)
- Status: decided
- Context: docs/BACKLOG.md, High: nothing bounded a chat turn end to end. The agent's turn ran as long as its parts did, holding the conversation's lock, a lock connection and every resource below it.
- Decision: config/turn.yaml: `deadline_s` 120, `answer_reserve_s` 45, `evidence_fetch_s` 10, `gateway_margin_s` 15 (app/resilience/turn.py, `Services.turn`). The runner opens the turn's scope (`deadline.within(deadline_s)`) around ownership, the graph and the terminal event. Each capability runs under min(its manifest's `limits.timeout_s`, what remains − `answer_reserve_s`), its isolated task created inside that scope so every call it makes inherits the end (ADR-0251); its own limit ends it `capability_failed` as before, the turn's reserve ends it `turn_timeout`. The evidence fetch runs under one bound, min(`evidence_fetch_s`, what remains − `answer_reserve_s`); past it the sources not fetched yet stay without text, as when a server cannot answer (span event `evidence.unavailable`, code `tool_timeout`). The answer runs under what remains. At the deadline the graph is cancelled with every task it owns (LangGraph cancels its node tasks; the capability's task is cancelled and drained, `deadline.drain`), and the turn ends with the error `turn_timeout` through the existing terminal-error path, the same persistence as any other terminal error; an error raised once the deadline has passed (an inner call it cut, reported in that call's own terms) is reported as `turn_timeout` too. The lease is released by `_locked`'s `finally`, as for every turn. Work that belongs to no caller runs in a detached context and is left running (status reads, ADR-0245; registrations, ADR-0254). Messages: `turn_timeout` in config/messages/errors.yaml, the gateway's catalogs and the web's ar.json and en.json; the code travels in an SSE error event, so neither OpenAPI document changes (T8, no version bump). Implementation note: a task awaiting its child task directly forwards its own cancellation into the child, and the CancelledError that comes back is the child's, which anyio does not recognise as its scope's; the capability's parent therefore waits with `asyncio.wait`.
- Consequences: Tested with a 3 s test deadline (tests/integration/test_turn_deadline.py, a slow model provider in the test container behind LiteLLM's test-only `slow-*` aliases): a hung capability ended `turn_timeout` at 3.01 s, a hung answer at 2.06 s and a trickling answer at 2.02 s (ADR-0258); another user's turn meanwhile 0.08–0.10 s (0.07–0.49 s alone); event-loop lag at worst 3.3 ms; nothing left waiting in the capability and no request left open at the provider; the next turn of the same conversation ran at once (no 409). A turn that needs more than 75 s of capabilities now ends `turn_timeout` instead of running on.

### ADR-0256: The gateway relays a turn under one total bound; its 60 s was never a header timeout (I3)
- Date: 2026-10-21
- Phase: standalone PR before phase 06 (`fix/timeout-budget`, implementation choice I3, scope D)
- Status: decided
- Context: docs/RESILIENCE.md said `gateway.agent.response-timeout` (60 s) bounded the agent's response headers only and the SSE body was unbounded, yet phase 05 lost eval cases at "the 60 s gateway timeout". I3 asked to establish with a test what ends a stream at 60 s before changing anything.
- Decision: Finding, established by test before any change (a probe with a 1 s response timeout and an agent streaming events for 3 s; kept as `StreamBoundTest.anyOtherAgentCallIsCutAtTheResponseTimeoutBodyIncluded`): Spring's `JdkClientHttpRequestFactory` applies its read timeout to the whole exchange from the request; at the timeout it closes the response body wherever the reading is (`IOException: closed`), and the relay then appended `agent_stream_interrupted`. The stream ended at 1.25 s of 3. So every turn was cut at 60 s total, body included, not a header timeout. Change: a turn stream has a client of its own whose factory bound is the relay's bound, `deadline_s + gateway_margin_s` from config/turn.yaml (135 s), read through the mechanism that reads config/docs.yaml (`gateway.turn.config`, GATEWAY_TURN_CONFIG, `TurnLimits`); every other agent call keeps `gateway.agent.response-timeout` 60 s, which is now documented as covering the body. Past the bound Spring closes the body, which closes the connection to the agent (so the agent's turn is cancelled as on a client disconnect), and the relay ends the stream with `turn_timeout` (new relay outcome `TIMED_OUT`, audited with `error_code` `turn_timeout`) instead of `agent_stream_interrupted`; the relay knows it from a deadline taken before the request leaves, so the client's own bound is never earlier. The agent's own `turn_timeout` at 120 s reaches the user first.
- Consequences: Tested (StreamBoundTest, 1 s response timeout and a 2 s relay bound): a stream still sending events ended `turn_timeout` at 2008 ms, not at 1 s; a silent agent at 2014 ms, and the next turn used a new connection (the old one was closed, not reused); a non-stream call whose body outlasts the response timeout is cut at 1032 ms. Such a cut read is mapped to 500 by the memory proxy (pre-existing; not changed here).

### ADR-0258: A hung or trickling answer ends up to LiteLLM's 1 s margin before the turn's deadline
- Date: 2026-10-21
- Phase: standalone PR before phase 06 (`fix/timeout-budget`; reconciles I2's test with I4)
- Status: assumed — Accepted on 2026-10-21
- Context: I2's tests ask for `turn_timeout` at the deadline ± 0.5 s for a hung capability, a hung answer and a trickling answer, with a 3 s test deadline. I4 has every model request tell LiteLLM to give up 1 s before the call's deadline, which for the answer is the turn's. LiteLLM honours it as its read timeout to the provider and reports it as an error (a status, or an error event mid-stream).
- Decision: The model gateway treats a failure with no more than 1.5 s left of its deadline as that deadline running out (`DeadlineExceeded`, inherited when the deadline was the turn's), so the turn ends `turn_timeout`, not `model_unavailable`. The answer cases of the test accept from deadline − 0.5 − 1 s (LiteLLM's margin) to deadline + 0.5 s; the capability case keeps ± 0.5 s. Measured: 2.06 s and 2.02 s for the answers, 3.01 s for the capability.
- Consequences: With the shipped 120 s deadline a stalled answer ends about 1 s before it. The owner may prefer the agent to hold its own deadline exactly (a smaller LiteLLM margin when little time remains).

### ADR-0257: Every model call has one deadline; LiteLLM gives up 1 s before it and the agent retries (I4)
- Date: 2026-10-21
- Phase: standalone PR before phase 06 (`fix/timeout-budget`, implementation choice I4, scope E)
- Status: decided
- Context: docs/BACKLOG.md, Medium: model calls ran under a 300 s per-operation timeout (between chunks, not total) inside capability limits of 30 or 60 s; LiteLLM retried once, up to 2 × 300 s inside the agent's 300 s; embeddings (300 s) and rerank (60 s) sat inside docs_search's 45 s.
- Decision: config/models.yaml `timeouts`: roles router, summarizer, judge, extractor 20 s, reasoning and reflection 90 s; embedding query 15 s, ingest 60 s; rerank 20 s; `retry` {min_remaining_s 10, backoff_s [0.5, 1.5]}. Each call (app/models/gateway.py, embeddings.py, app/rag/rerank.py) runs under `deadline.within(ceiling)`, so min(ceiling, what remains of the call around it), from send to the last chunk. Each request carries the client's per-operation timeout 1 s past what remains and LiteLLM's per-request `timeout` 1 s before it, so the agent's deadline decides. LiteLLM: `num_retries: 0`, `request_timeout: 85` (infra/litellm/config.yaml, and config.mock.yaml for tests). The agent retries at most once, only before the first streamed token, only on 429, 5xx or a connection error, only with ≥ 10 s left, after a uniform 0.5–1.5 s. Any LiteLLM error, an error event mid-stream included (`openai.APIError`, which was not caught before), is `ModelUnavailable`; one with ≤ 1.5 s left is the deadline running out (ADR-0258). Past its own ceiling a call is `ModelUnavailable`; cut by the deadline around it, it raises `DeadlineExceeded` for that deadline's owner. A stream is closed however the call ends, shielded from the cancellation. `embed` is a query, `embed_many` an ingest batch. The broker runs a tool's handler under `deadline.within(timeout_s)` instead of `asyncio.wait_for`, so the calls inside a tool (docs_search's embedding and rerank) see its bound. Found by the flood tests and fixed on these paths (rule 12d): an embedding answer is read incrementally and refused past 48 KiB per input text before it is parsed (a 2 MiB flood stalled the agent's loop 490 ms when parsed whole); a rerank answer likewise past 16 KiB + 256 B per document. The heavy → standard fallback stays.
- Consequences: Tested (tests/integration/test_model_bounds.py, a slow provider in the test container behind `slow-*` aliases, each while another user's call runs; event-loop lag bound 100 ms): see docs/PROGRESS.md for every timing. Two residuals, measured and recorded in docs/RESILIENCE.md: (1) LiteLLM closes the provider's request at once when the agent cancels a stream it is already sending (0.05 s), but before the first byte, and for embeddings, it holds it until the request's own timeout, 1 s before the call's deadline (a call cancelled at 1 s with a 6 s deadline: closed 5.03 s after send); never past the call's deadline, and LiteLLM's `request_timeout` 85 s bounds it for any caller. (2) LiteLLM relays a flooding stream on its own event loop: another user's call through it waited 4.02 s while 20 000 one-character chunks were relayed (the agent's loop lag stayed 32 ms); docs/BACKLOG.md phase 12.

### ADR-0259: An MCP call is nested in its caller's deadline: slot, attempt, re-send, SDK (I6)
- Date: 2026-10-21
- Phase: standalone PR before phase 06 (`fix/timeout-budget`, implementation choice I6, scopes E and F)
- Status: decided — Accepted on 2026-10-21
- Context: docs/BACKLOG.md: the broker's bound on an MCP tool (timeout_s + 5) was shorter than what the client could take with a full queue (a wait of timeout_s, then an attempt of timeout_s + 2), so the broker's tool_timeout fired before the client's tool_busy (Medium); the SDK's read timeout equalled the transport's deadline, so which fired first was a race (Low).
- Decision: The broker's bound on an MCP tool is 2 × timeout_s + 2, as ADR-0237 described, cut to what remains of the caller by the broker's `deadline.within`; a transport operation (`call_transport`) gets the same bound. In app/mcp/client.py: when no slot is free, the wait for one is at most min(timeout_s, remaining − timeout_s − 1), so the call keeps its own timeout_s once it has one; when that is ≤ 0 the call is `tool_busy` at once with the breaker untouched (read as: a call that would have to wait; a free slot is taken at once whatever is left, since failing a call as busy while a slot is free would be wrong). One attempt deadline, min(timeout_s, remaining − 0.5), covers opening or taking a session, the request, and the I9 re-send with its wait for the next generation (it replaces the `timeout_s + 2` backstop). When the caller's deadline set that end (inherited), its expiry is no verdict on the server, as a caller's cancellation is not (ADR-0244); its own timeout_s still counts (ADR-0237). The SDK's `read_timeout_seconds` is timeout_s + 1, past the transport's wall-clock deadline (timeout_s from send), which therefore always decides.
- Consequences: Tested (tests/integration/test_mcp_bounds.py, servers in threads of their own): a hung server and a trickling one, timeout_s 10, inside a caller with 1.5 s left: tool_timeout at 1.01 s and 1.00 s, no breaker failure counted, the request closed 0.02 s after, another user's call on the same server 20 ms and 11 ms (2 ms alone), loop lag at worst 2.9 ms; a full queue with 5 s left: tool_busy in 2.0 ms, breaker untouched; with 30 s left the call waited 2.71 s for the slot and succeeded; a server restarted with every initialize held 5 s: the re-send waited for the next generation until the attempt deadline, tool_timeout at 2.00 s (timeout_s 2), no verdict; the SDK's read timeout is timeout_s + 1. The existing phase 05 tests (transport floods and trickles at timeout_s, sessions, breaker) pass unchanged.

### ADR-0260: The gateway drops an idle connection to the agent before the agent does (I8)
- Date: 2026-10-21
- Phase: standalone PR before phase 06 (`fix/timeout-budget`, implementation choice I8, scope F)
- Status: decided
- Context: docs/BACKLOG.md, Low: the gateway's JDK HttpClient kept idle connections to the agent 30 s while uvicorn closed them after 5 s (its default), so the gateway could send a request on a connection the agent had just closed.
- Decision: The agent runs uvicorn with `--timeout-keep-alive 75` (services/agent/Dockerfile; docker-compose.dev.yml likewise). The gateway's idle keep-alive is set explicitly from `gateway.agent.keep-alive: 30s` (application.yml): `HttpClientKeepAlive`, an EnvironmentPostProcessor registered in META-INF/spring.factories, sets `jdk.httpclient.keepalive.timeout` before any bean exists, because the JDK reads it once, when its first HttpClient is created, and the Keycloak admin client may be built before the agent client. Checked first in the image (eclipse-temurin 21.0.12): the JDK's effective value is 30 s (`HttpClientImpl.KEEP_ALIVE_TIMEOUT`), so the value is unchanged and now reviewed rather than inherited. Client idle 30 s < server idle 75 s.
- Consequences: Tested: the running gateway's property is 30, and it follows the configured value (HttpClientKeepAliveTest); a smoke step sends two turns 10 s apart through one gateway, both complete, and the gateway's log holds no connection-reset line for the whole run. An idle agent connection costs uvicorn up to 75 s of an idle socket.

### ADR-0261: The timeout budget is a check in make test (I9)
- Date: 2026-10-21
- Phase: standalone PR before phase 06 (`fix/timeout-budget`, implementation choice I9, scope G)
- Status: decided
- Context: docs/RESILIENCE.md's timeout budget table was written by hand after PR #10; nothing kept a later configuration change from putting an inner bound past its outer one again.
- Decision: scripts/check_timeout_budget.py reads config/turn.yaml, config/models.yaml, config/docs.yaml, config/mcp.yaml, every capability manifest (and docs_search's tool bound in its tools.py), infra/litellm/config.yaml, the gateway's application.yml, the agent's Dockerfile and the margins in the agent's code, and checks every nested pair of the table whose two values are configured (21 today): the turn in the relay, the reserve, the evidence fetch and every capability in the room before the answer, the answer's ceiling in the turn, LiteLLM's backstop and retries, the helper ceilings in the shortest capability, docs_search in docs and a query's embedding plus rerank in docs_search, each MCP server's broker bound in the capabilities that declare its tools and the RAG server's call in docs_search, the registration in the gateway's call, the upload's stall timeout in its deadline, the keep-alive pair, and the margins that make the transport and the agent's deadline decide. Bounds nested by construction (min(ceiling, what remains)) are checked where a configured value still decides. `--self-test` applies nine violating changes and requires each to fail. `make test` runs both (`make test-budget`), reported as "timeout budget N / 0 / 0".
- Consequences: A change to any of those files that nests a bound wrongly fails `make test`. A new nested pair needs a line in the script and a row in docs/RESILIENCE.md.

### ADR-0262: Turn limits per user, per tenant and platform-wide, in the agent (PR #12 review, decision 1)
- Date: 2026-10-21
- Phase: standalone PR before phase 06 (`fix/timeout-budget`, fix session on the PR #12 review, decision 1)
- Status: decided
- Context: Review of PR #12, (4)1: nothing limited one user's turns but the platform's lock pool (LOCK_POOL_SIZE, 20), so one user with 20 conversations held every turn slot and every other user got 503 `service_busy` for as long as those turns ran; the PR's own 120 s deadline had doubled how long that was (the gateway's 60 s cut no longer ended them). CLAUDE.md rule 12a asks for limits at three levels.
- Decision: The agent, where the lock pool lives, takes a turn slot before ownership and the lock (`TurnRunner.admit`, app/resilience/limits.py `ConcurrencyLimiter`): config/turn.yaml `per_user_max` 2; `per_tenant_max` optional, default null meaning the platform capacity, so a single-entity deployment has no tenant restriction of its own; when set it is each tenant's share for a multi-tenant deployment, with `per_tenant_overrides` by tenant id; the platform capacity is LOCK_POOL_SIZE (20 in development, sized per deployment). Past the user's or the tenant's limit the turn is refused at once, 429 `too_many_turns`; the slot is released with the lease, once, however the turn ends. The counters live in the agent process (one replica in v1); docs/BACKLOG.md phase 12 moves them to Redis with the other limits. Custom code (rule 12i), a counter per user and per tenant under one total, refused at once: an `asyncio.Semaphore` per key waits rather than refuses and holds no three levels at once; aiolimiter limits a rate, not what is in flight; Bucket4j or Envoy's rate-limit service over Redis are the phase 12 shape for more than one replica and are not in the stack today; a row in docs/RESILIENCE.md "What we built". Production capacity is a resource decision measured by the phase 12 load test (docs/BACKLOG.md). Messages: `too_many_turns` in config/messages/errors.yaml, the gateway's catalogs, the web's ar.json, en.json and lib/errors.ts; the gateway maps the agent's 429 to it (`AgentRefusedException`). The agent's `POST /v1/turns` and the gateway's `POST /v1/chat/turns` document the 429, so both OpenAPI documents move 2.2.0 → 2.3.0, additive (T8); the events schema does not change.
- Consequences: Tested (tests/integration/test_turn_limits.py, hung answers behind the slow provider): user A's 20 turns, 2 run and 18 are refused at once; user B's turn meanwhile is admitted within 1 s and completes in its normal time; with `per_tenant_max` 10, tenant X's eleventh turn is refused while tenant Y's runs; by default one tenant's ten users hold all 20 slots and the next turn is refused as the platform's (ADR-0270). A user who wants a third answer at once waits for one of theirs to finish.

### ADR-0263: A post-answer reserve; once the answer's last token is sent the turn never ends turn_timeout (PR #12 review, decision 2)
- Date: 2026-10-21
- Phase: standalone PR before phase 06 (`fix/timeout-budget`, fix session on the PR #12 review, decision 2)
- Status: decided
- Context: Review of PR #12, (4)2: the answer reserve covered the answer only; the guards on the whole answer, persistence, the summary and memory ran in what remained, so an answer ending near the deadline was followed by `turn_timeout` with no retract, the exchange possibly not persisted and the whole-answer OUTPUT check skipped.
- Decision: config/turn.yaml `post_answer_reserve_s` 30 and `persist_s` 10. The answer's model calls, its validation retry included, run under min(their ceiling, what remains − `post_answer_reserve_s`) (app/graph/nodes/answer.py). The guards on the whole answer (OUTPUT, DATA) run under a horizon `persist_s` before the turn's deadline (`deadline.horizon`): the node is not cancelled there; each check bounds itself in its own task (`deadline.within` in GuardEngine._check), so a check the horizon or its checker's ceiling cuts follows its policy's on_error, never skipped. Each checkpoint write takes at most min(`persist_s`, what remains) (`BoundedSaver`, app/graph/checkpointer.py). fold_summary and write_memory run only when what remains is at least their model role's ceiling (summarizer, extractor: 20 s), under that ceiling; otherwise they are skipped and logged, `summary_skipped` / `memory_skipped` (span event and log). Once the answer's last token is sent (streaming: the answer node's end; buffered: release_answer), `TurnContext.answer_sent` is set, the runner moves the turn's end to at least `post_answer_reserve_s` from then (`Budget.extend`), and a deadline reached after it ends the turn `done` with what was answered, never `turn_timeout`. DataStream waits for its task with `asyncio.wait` and drains it, as the capability's parent does (ADR-0255).
- Consequences: Tested (tests/integration/test_post_answer.py, deadline 8 s, answer reserve 4 s, post-answer reserve 2 s, persist 0.5 s): the probe capability takes until the answer's reserve, the answer streams for 1.8 s and ends near its bound; the whole-answer pass retracts and re-sends the redacted text, the exchange is persisted, memory is skipped, the turn ends `done`; a judge that never answers at OUTPUT, cut by its own ceiling, follows its policy's on_error (`done` when open, an error when closed); cut at the horizon it leaves the DATA pass no time, whose fail-closed checks end the turn `guard_unavailable` (ADR-0270); summary and memory are skipped and said so when no time remains. The checker ceilings of two judged checkpoints (OUTPUT then DATA, 20 s each) can exceed the reserve; then the later one ends on its on_error.

### ADR-0264: The turn's deadline opens on arrival; ownership and the lock within admission_s; the gateway bounds the headers (PR #12 review, decision 5)
- Date: 2026-10-21
- Phase: standalone PR before phase 06 (`fix/timeout-budget`, fix session on the PR #12 review, decision 5)
- Status: decided
- Context: Review of PR #12, (1) and (f): the agent's claim (ownership reads) and lock ran before the turn's deadline opened, bounded only by the gateway's 135 s, and the turn stream's client had lost the 60 s header bound I3 said stays.
- Decision: The route takes the arrival time (`deadline.now()`); `TurnRunner.admit` opens the turn's end there (`deadline_s` from arrival) and takes ownership and the lock within config/turn.yaml `admission_s` 10 (`asyncio.timeout`, delivered once, so a lock connection the pool takes back is not cancelled in turn); past it, 503 `service_busy`, the slot released. The graph runs under what remains of that same end. The gateway's turn stream has a request factory of its own (`TurnStreamRequestFactory`, the JDK client itself): the agent's response headers within config/turn.yaml `gateway_headers_s` 15 (the JDK request timeout, which bounds the response's arrival), then the whole exchange within `deadline_s + gateway_margin_s` 135 s, the body closed there; past the header bound the turn is 502 `agent_unavailable`. Every other agent call keeps `gateway.agent.response-timeout` 60 s.
- Consequences: Tested: a hung ownership read in the database (`pg_sleep`) is 503 `service_busy` at the admission bound, the slot released and the pool not waiting (tests/integration/test_turn_deadline.py); at the gateway, an agent that never sends its headers is `agent_unavailable` at the header bound and a stream held past the relay bound ends `turn_timeout`, each while another user's turn completes at once (StreamBoundTest). scripts/check_timeout_budget.py checks admission_s < gateway_headers_s < the relay bound.

### ADR-0265: A registration counts toward its user's uploads in flight until it ends; background work takes half a server's slots and one deadline; the upload answers the stored state (PR #12 review, decisions 4 and 6)
- Date: 2026-10-21
- Phase: standalone PR before phase 06 (`fix/timeout-budget`, fix session on the PR #12 review, decisions 4 and 6)
- Status: decided
- Context: Review of PR #12, (1), (4)3 and (e): the gateway's per-user upload slot was released when the upload answered `pending`, while its registration went on holding a server slot (up to 30 s of wait and a 30 s attempt), so two users uploading against a slow `register` could fill the RAG server's slots and every other user's docs search waited and failed `tool_busy`. On the internal backend a push that landed before its call failed could leave the upload answered `failed` while the record became indexed; `facade.fetch` did not drop failed records.
- Decision: (4) `AttachmentService` counts each registration toward its user's `limits.max_concurrent_uploads` (2) until it ends, answered or still in the background; a tenant cap is optional (`max_concurrent_uploads_per_tenant`, none by default, `tenant_concurrent_uploads` by tenant id), and `max_concurrent_uploads_total` bounds them all; past a cap `register` is refused at once, 429 `too_many_uploads`, which the gateway answers as its own and whose stored object it discards. A call in a detached context (`deadline.detached()`: a registration past the upload's answer, a status read past its bound) takes one of the server's background slots, at most `max_concurrency // 2`, before a slot (a second semaphore per server in app/mcp/client.py), so callers always find some; its slot wait (at most half) and its attempt share one deadline of `timeout_s`, and its own timeout still counts toward the breaker. (6) `register` answers the record as stored after the failure: `mark_register_failed` returns the updated row, and when no row matched (the internal worker had moved the record on) the record is read again. `facade.fetch` drops the text of a chunk the facade found in a file whose registration failed.
- Consequences: Tested (tests/integration/test_upload_registration.py; tests/integration/test_mcp_bounds.py): the review's setup, two users uploading two files every 20 s against a `register` held 25 s: their second rounds are refused `too_many_uploads`, and a third user's 40 searches over 41 s each answer in their normal time; a user's third concurrent upload is refused at once and accepted again once a registration ends; a trickling `register` ends the file `failed` `register_failed` by its one deadline (timeout_s 3), a flooding one at its byte cap, while another user's search answers at once; a failure after the upload answered `pending` ends `failed`, its chunks never returned; on the internal backend the upload answers `indexed` when the worker won the race and `failed` when the failure was marked first (the worker then cannot index it); six background calls on a server with max_concurrency 4 never have more than 2 open and a caller's call takes a slot at once. The status reads of ADR-0245 are background work too: at most `timeout_s` now, in at most half the slots (ADR-0270). A refusal comes after the body was read, at registration (ADR-0270).

### ADR-0266: A byte cap under the OpenAI SDK on every answer from LiteLLM, any status (PR #12 review, decision 3)
- Date: 2026-10-21
- Phase: standalone PR before phase 06 (`fix/timeout-budget`, fix session on the PR #12 review, decision 3)
- Status: decided
- Context: Review of PR #12, (i): the OpenAI SDK reads an error answer whole (`aread`) and parses it as JSON before raising, so a provider error of any size was read and parsed on the agent's event loop, past the embedding cap that applied to 2xx answers only.
- Decision: app/models/transport.py `CappedTransport`, the transport of the model and embedding gateways' HTTP clients (httpx2, the SDK's own client, now a declared dependency): it counts an answer's bytes as they are read and stops the read past its cap, closing the connection, before the SDK or anything else parses it. Error answers and non-stream 2xx answers: 1 MiB, refused at once when the Content-Length says more; a streamed 2xx answer: 1 MiB between two newlines, so no single event is buffered past it; embeddings' 2xx answers keep their cap per input text (48 KiB), read before parsing. Every request asks for an uncompressed answer, so the bytes counted are the bytes parsed. Past a cap the call is ModelUnavailable, never retried. The rerank client already read every status capped. Custom code (rule 12i): neither httpx nor the OpenAI SDK caps a response's size (the SDK has no option for it, httpx's limits are on connections), and LiteLLM's proxy does not cap what it relays; an httpx transport is the library's own extension point; a row in docs/RESILIENCE.md "What we built".
- Consequences: Tested (tests/integration/test_model_bounds.py): a 2 MiB 500 and a 2 MiB 429 body are refused by code 1 ms after LiteLLM's answer arrives, unretried, the agent's loop lag at most 3.7 ms. Measured residual (docs/RESILIENCE.md): LiteLLM itself spends about 5 s per MiB turning the provider's error into its own before the agent sees a byte (0.04 s, 1.34 s, 5.32 s and 10.57 s for 0, 256 KiB, 1 MiB and 2 MiB), on its own loop, so another user's call through it waited 10.3 s; "fails in < 1 s" holds for the agent's part only. docs/BACKLOG.md phase 12, with the flood residual of ADR-0257.

### ADR-0267: Recording a turn's guard matches and releasing its lease are bounded (PR #12 review, decision 7)
- Date: 2026-10-21
- Phase: standalone PR before phase 06 (`fix/timeout-budget`, fix session on the PR #12 review, decision 7)
- Status: decided
- Context: Review of PR #12, (a) and (b): `_record_guard_events` ran after the turn's deadline while the turn held its lock, and `TurnLease.release` unlocked with no timeout, so a hung database held the stream's end and pinned the lock connection.
- Decision: Each is bounded to 5 s (`BOOKKEEPING_TIMEOUT_S` in app/api/runner.py, which also bounds the turn's place in the conversation list; `RELEASE_TIMEOUT_S` in app/api/locks.py) with `asyncio.timeout`: the cancellation is delivered once, so closing and returning the connection after it are not cancelled in turn. On timeout the event is logged and the connection closed: the guard write's connection, left mid-query, is discarded by the pool; the lease closes its own, which ends the session and with it the advisory lock, and the pool discards it.
- Consequences: Tested with the database holding the statement (`pg_sleep`, tests/integration/test_turn_lock.py, bounds set to 1 s): the unlock returns at 1.00 s with its connection closed and the conversation free; a turn whose guard write hangs ends at 1.07 s (0.07 s normally), its lock free and the pool not waiting.

### ADR-0268: A task that ignores its cancellation is abandoned, logged and counted (PR #12 review, decision 8)
- Date: 2026-10-21
- Phase: standalone PR before phase 06 (`fix/timeout-budget`, fix session on the PR #12 review, decision 8)
- Status: decided
- Context: Review of PR #12, (a): `deadline.drain` gave a cancelled child 1 s and then went on without it, silently; a capability that swallows its cancellation keeps running unbounded. In-process Python cannot stop it (ADR-0054).
- Decision: After the drain's grace, a task still running is logged `task_abandoned` with the node that owned it (`run_capabilities:<slug>`, `answer:stream`) and counted (`deadline.ABANDONED` and the OpenTelemetry counter `agent.tasks_abandoned`, exported once a meter provider is configured; none is in v1). Recorded as a residual in docs/RESILIENCE.md: process isolation of capabilities (ADR-0054) is what would stop it, phase 12. No other change.
- Consequences: Tested: a probe capability that swallows its cancellation for 8 s ends its turn `turn_timeout` within the deadline plus the grace, is logged and counted once, and another user's turns across the expiry and the drain take their normal time (tests/integration/test_turn_deadline.py; tests/unit/test_deadline.py).

### ADR-0269: The timeout budget's rules live in one module; each rule has its own mutation; a missing rule fails (PR #12 review, decision 9)
- Date: 2026-10-21
- Phase: standalone PR before phase 06 (`fix/timeout-budget`, fix session on the PR #12 review, decision 9; amends ADR-0261)
- Status: decided
- Context: Review of PR #12, (g) and (3): four pairs of scripts/check_timeout_budget.py compared 0 with a constant the code might not use; nine mutations covered nine of 21 pairs and passed when any pair failed; a pair vanished silently when a file changed shape; the sandbox memory, the broker bound against the slot wait and attempt, and the dev overlay's keep-alive were not checked.
- Decision: The rules are in services/agent/app/resilience/budget.py (standard library only), which the script imports, so a later endpoint that saves one of these files can apply them to what it is about to write. The four "0 < constant" pairs are replaced by checks that the code uses the constant where it nests the bound (`*-used`). Every rule has its own mutation, and the self-test requires that each makes its own rule fail. EXPECTED lists the names the shipped configuration must produce; a missing one fails. New rules: each document service's sandbox children (workers × memory_mb + parent_reserve_mb ≤ mem_limit, read from docker-compose.yml), each MCP server's slot wait and attempt in its broker bound, the dev overlay's keep-alive, and this session's admission, header, post-answer and persist bounds.
- Consequences: `timeout budget 31 / 0 / 0`, self-test 31 / 0 / 0. A new nested pair needs a rule, its name in EXPECTED, a mutation and a row in docs/RESILIENCE.md.

### ADR-0270: The details this session filled in
- Date: 2026-10-21
- Phase: standalone PR before phase 06 (`fix/timeout-budget`, fix session on the PR #12 review; fills gaps in ADR-0262 to ADR-0266)
- Status: assumed — Accepted on 2026-10-21
- Context: Details the owner's decisions left open.
- Decision: (1) A turn refused because the platform's capacity is full is 503 `service_busy` at once, as a full lock pool was; 429 `too_many_turns` is for the user's and the tenant's limits. (2) The registration cap is enforced where the registration lives, at the agent's `register`, after the gateway has read the body and stored it; the gateway discards the stored object of a refused upload. The gateway's own 2-per-user cap on bodies in flight stays in front of it. (3) The background rules (half the slots, one deadline of timeout_s) apply to every call in a detached context, the ADR-0245 status reads included, which now live at most timeout_s (30 s for rag) instead of 2 × timeout_s + 2. (4) `facade.fetch` drops the chunks the facade itself found in a file whose registration failed; it cannot tell the file of an opaque reference it never returned. (5) The persist bound is on every checkpoint write of a turn (the write after persist_state is the exchange's); the guards' horizon is `persist_s` before the deadline, so a judge that uses the whole window leaves a following checkpoint none, whose checks then follow their on_error. (6) `agent.tasks_abandoned` uses the OpenTelemetry metrics API with no meter provider configured in v1. (7) httpx2 is declared directly: the byte-capped transport must be of the client the OpenAI SDK uses.
- Consequences: The owner may prefer a gateway-side check before the body, a 429 for a full platform, or the status reads outside the background rules. With (3), tests/integration/test_mcp_sessions.py's ten-file list test runs its server with max_concurrency 20 (ten background slots) instead of 16; no test was removed.

### ADR-0271: The airgap relay closes each direction of the gateway's connections on its own
- Date: 2026-10-21
- Phase: standalone PR before phase 06 (`fix/timeout-budget`, narrow session on the smoke-airgap upload check)
- Status: decided
- Context: `make smoke-airgap` failed three times at the R3 check "a 30 MB file → the JSON 413, refused before its body is read, no reset" (docs/PROGRESS.md): the JSON 413 arrived whole and curl then got a connection reset (exit 56). The gateway answers 100 Continue at once (Tomcat), so the client starts sending, refuses on the declared size without reading the body, and closes (review round 2, finding 2). Measured under the airgap overlay from a fresh `make up`, 30 checks each, the first right after startup, paced under the 20 uploads per minute: main (a371373) 5 resets in 30 (uploads 1, 3, 5, 20, 22), this branch (d155407) 1 in 30 (upload 9); every answer the JSON 413. Not a regression: the reset predates the branch. Without the relay (`make smoke`) the check never reset. The mechanism, consistent with the change's effect below but not traced packet by packet: the relay, nginx's `stream` proxy (infra/airgap/nginx.conf), closed the client's connection as a whole when the gateway closed its side; with the client's unread body bytes still in nginx's receive buffer, that close is sent as a reset, which can reach the client before it has finished with the answer.
- Decision: The gateway's relay sets `proxy_half_close on` (nginx 1.21.4 and later; the image is 1.27.5): each direction of the TCP connection is closed on its own, so the gateway's answer and its end reach the client in order and the client closes its side. Only the gateway's server; the others are unchanged. The R3 check stays strict and now says how it is strict, in scripts/refused_unread.sh, which smoke sources: it fails when the connection is reset or broken (curl's exit), when the answer is not 413, when it is not the JSON `file_too_large`, and when the client had sent the whole body before the answer (a gateway that read the body first could answer only then; Tomcat's 100 Continue means the client always starts sending, so "before its body is read" is that the answer comes while the body is still incomplete).
- Consequences: After the change, from a fresh start, 0 resets in 30 checks. Against fake endpoints the check fails each way it should: one that reads the whole body and then answers the JSON 413 (31 457 491 of 31 457 289 bytes sent), one that answers 413 without JSON, and one that resets (curl exit 56); the gateway behind the relay passes (about 1.5 MB of 31.5 MB sent). Production's ingress is the entity's own (docs/RESILIENCE.md, trap 1); the same half-close setting belongs there.

### ADR-0272: A 404 on the I9 re-send is no verdict; the path ADR-0249 missed (scope A)
- Date: 2026-10-22
- Phase: standalone PR before phase 06 (`fix/timeout-budget`, scope A, narrow fix session; amends ADR-0249)
- Status: decided
- Context: `make test` of 2026-10-21T16:18Z (f9d855b) counted one breaker failure against a healthy server in tests/integration/test_mcp_generation_reset.py `four-calls` (iteration 5); the file then passed three times alone. Reproduced at bbd342d (2026-10-22T08:54Z–09:22Z) with a scratch harness that records, at every `Breaker.failure`, the exception that led to it, the lines of app/mcp/client.py it went through and `_attempt`'s locals: 2000 seeded iterations of four calls counted 3 (iterations 124, 1644, 1897; 0.15 % of iterations); with the restart pinned to each step of a call, 300 iterations each, pooled and with an empty pool (every call opening its own session), only the restart landing while a call is held at the server (the read) counted any: 7 in 300 (2.3 %); the slot, session open, initialize, initialized notification and send steps counted none. All ten took one path: a restart that lands while every request is being held is seen by none of them (requests already open finish), so the generation stays connected on sessions the server has forgotten; a later call's request on one is answered 404, the call ends that generation and waits for the next, which is opened, and the server restarts again before the call's I9 re-send arrives (`_attempt` line 616, `_send`): the re-send's own 404 came back as the SDK's `McpError` "Session terminated" (code 32600), which `_attempt` handled only on the first send, so it fell to the generic `McpError` branch: `_Failed(tool_unavailable, server_failed=True)`, one failure counted. State at the count: `resend` true, `waiting` false, the re-send's session of generation N+1 where the first was of N, the connection connected on N+1. ADR-0249 closed the wait for the next generation and a call finding none; it did not consider the next generation being forgotten in its turn.
- Decision: A "Session terminated" (404) on any send of an attempt is an answer from a server that is up: it is never a server failure. On the re-send the call fails `tool_unavailable` with no verdict (`server_failed` None: the breaker is left as it was, a trial is released, ADR-0244), and the generation the re-send was sent on is ended when it is still the current one, so later calls do not take its forgotten sessions; its session is closed, not pooled. The call is still re-sent once only (I9). Everything else ADR-0249 decided stands, including that a session failing to open still counts.
- Tested (tests/integration/test_mcp_generation_reset.py, the assertion unchanged: no failure counted, the breaker closed, after every iteration): two scenarios added, with `RestartingMCPServer.restart_during_call` and `restart_at_resend` (tests/fixtures/slow_mcp.py): `restart-during-a-read`, four calls, the restart landing at a seeded point of one call's hold, 300 iterations; `restart-again-at-the-resend`, four calls, the server restarting again when the first call after a 404 arrives, 50 iterations. On the code before the fix both failed (2026-10-22T09:23:27Z): `restart-during-a-read` `AssertionError: (3, 1, ['tool_unavailable'])`, `restart-again-at-the-resend` `AssertionError: (1, 1, ['tool_unavailable'])`. After it, five consecutive runs of the file at its full count passed, 7 / 0 / 0 each; the harness again (2000 iterations of four calls, every pinned step, pooled and cold) counted none.
- Consequences: A healthy server that restarts twice within one call's attempt still fails that call `tool_unavailable` (in the runs: 122–125 of 200 calls in `restart-again-at-the-resend`), now without counting against the server; waiting for the next generation rather than failing stays in docs/BACKLOG.md phase 12 (ADR-0249). The test file takes about 195 s (about 80 s before).

### ADR-0273: The restart breaker race stops at a known residual; the scope A test asserts the breaker never opens
- Date: 2026-10-22
- Phase: standalone PR before phase 06 (`fix/timeout-budget`, scope A, owner decision of 2026-10-22; amends the test of ADR-0249 and ADR-0272)
- Status: decided
- Context: The narrow fix session on the scope A race (one breaker failure counted against a healthy server during a restart in `make test` of 2026-10-21T16:18Z) had reproduced it (3 in 2000 seeded iterations of four calls; 7 in 300 with the restart landing during a call's read), named and fixed one path (ADR-0272) and tested the fix (five consecutive green runs of the test file; 0 counted in the same harness runs), when the owner stopped the investigation: server restarts are planned maintenance with notice, and the breaker counts consecutive failures only, so a single stray count is reset by the next success and cannot open it.
- Decision: The investigation stops. The fix of ADR-0272 stays: it is complete and its tests pass. A rare stray count during a restart is a known residual, listed in docs/BACKLOG.md phase 12 with the reproduction rates measured. The scope A test (tests/integration/test_mcp_generation_reset.py, now `test_a_healthy_servers_restart_never_opens_the_breaker`) asserts what users would notice: the breaker never opens during a restart, in any of its seven scenarios, checked at every failure the breaker is told of as well as at the end of each iteration, and is closed after every iteration; the failures counted are printed, not asserted. Iteration counts unchanged (50 each; 300 for `restart-during-a-read`); no test skipped or removed.
- Consequences: A path other than ADR-0272's, if one exists, may still count a stray failure during a restart; it would fail the test only if it counted five in a row. Checked against a mutation (each generation reset counting five failures): the test fails (`four-calls` at iteration 0, `restart-again-at-the-resend` at iteration 1).

### ADR-0274: A timeout counts only when the server held the request its full timeout_s; background work keeps its own timeout_s after its slot wait
- Date: 2026-10-22
- Phase: standalone PR before phase 06 (`fix/timeout-budget`, narrow fix session on review round 2 of PR #12, finding (1); supersedes the one deadline of ADR-0265 and ADR-0270 (3))
- Status: decided
- Context: Review round 2, finding (1): a background call (every registration past the upload's answer, every status read past its bound) had one deadline of `timeout_s` for its slot wait (up to half of it) and its attempt, so a call that waited W seconds behind other users' background work had an attempt of `timeout_s − W`, whose expiry was not inherited and counted against the breaker (`_Failed(TIMEOUT, True)`). A server answering `register` in 25 s, inside its 30 s, was judged failed whenever the wait passed 5 s: six users uploading two files each at 0, 12 and 30 s, with no search to reset the count, counted 8 failures (the 5th opens the rag breaker for every user for 30 s), and each of those files ended `register_failed`, final. Background work had lost the guarantee ADR-0259 gives a caller's call. This broke ADR-0244's principle that only the server's own outcome decides.
- Decision: A timeout counts against the breaker only when the server itself held the request for its full `timeout_s` from the moment it was sent. An attempt shortened and then cut gives no verdict, as a caller's cancellation does not (ADR-0244). Shortened means: by its caller's deadline (inherited, ADR-0259), by a wait for the next generation (ADR-0249), or because it was an I9 re-send, sent after the attempt had begun. Background work gets ADR-0259's guarantee:
  - It still takes a background slot first, at most `max_concurrency // 2` (ADR-0265).
  - Its slot wait is bounded on its own, as a caller's is: at most min(`timeout_s`, remaining − `timeout_s` − 1), where remaining is under the broker's `call_transport` bound of 2 × `timeout_s` + 2; when no wait is left, `tool_busy` at once.
  - Once it has a slot, its attempt has the full `timeout_s`. No wait is taken from it, so its timeout is again the server's outcome.
  - A free slot is taken at once.

  `_attempt` has no ceiling parameter: it always runs within `timeout_s`, cut by the caller's deadline less 0.5 s.
- Consequences:
  - A background call lives at most 2 × `timeout_s` (60 s for rag): up to 30 s of slot wait, then 30 s of attempt. Under ADR-0265 and ADR-0270 (3) it lived `timeout_s`. That is still inside the broker's bound, and the budget rule `slot-and-attempt-in-broker` checks it.
  - A re-send that times out is now no verdict. The next call to a server that really hangs, sent on a fresh session, is held its full `timeout_s` and counts.
  - Tested:
    - The reviewer's reproduction (tests/integration/test_upload_registration.py `test_registrations_queued_behind_other_users_never_count_against_a_server_answering_in_time`) runs on the reference RAG server with `register` held 25 s, `timeout_s` 30 and rag's 8 slots, so 4 background. Users A to F upload 2 files each, A and B at 0 s, C and D at 12 s, E and F at 30 s, with no search running. The test asserts that no failure is counted, the breaker stays closed and every file ends indexed. On the code before the fix (07d1b68, 2026-10-22T14:25Z) it failed: `AssertionError: [1, 1, 1, 1, 1, 1, ...]`, 8 failures counted, and 8 registrations failed with `ToolFailure`. After it (2026-10-22T14:29Z–14:37Z, with the four MCP and upload test files and the unit tests, 529 / 0 / 0): 0 failures counted, the breaker closed, all 12 files indexed, the last registration ended by 75 s, worst loop lag 14.5 ms.
    - A server that really holds a request past `timeout_s` still counts (tests/integration/test_mcp_bounds.py `test_background_work_takes_at_most_half_the_slots_and_keeps_its_own_timeout`). There are 6 hung background calls, `timeout_s` 4 and 2 background slots. Two time out at 4 s and count. Two wait about 3 s, then have their own full 4 s and count. Two find no slot within their wait and end `tool_busy`. That makes 4 failures counted, while a caller's call meanwhile answers in its normal time. Measured: timed out at 4.00, 4.00, 7.02 and 7.02 s, busy at 4.00 s and 4.00 s, a caller's call 41 ms (1 ms alone). The scope A file passed unchanged: 0 failures counted, never opened.

### ADR-0275: The timeout budget's rules of a capability, tool or server are generated from what the configuration declares
- Date: 2026-10-22
- Phase: standalone PR before phase 06 (`fix/timeout-budget`, narrow fix session on review round 2 of PR #12, finding (3); amends ADR-0269)
- Status: decided
- Context: Review round 2, finding (3): services/agent/app/resilience/budget.py, a core module, named the shipped capabilities and their tools.
  - The check that a tool fits its capability's limit existed only for `docs_search` in `docs`.
  - EXPECTED and MUTATIONS named `directions`, `docs`, `general`, `docs_search` and the servers `routing` and `rag`.
  - So the kb, web and sql tools (phases 08 to 10) would go unchecked until core was edited, and removing or renaming a capability would fail `make test` (CLAUDE.md rule 3).
  - The `helper-in-shortest-capability` mutation (router 30) also depended on `general`'s 30 s: with `general` removed, it violated nothing.
- Decision: Rule families, each generated from the values the script reads, with no capability, tool or server named:
  - `capability-in-room:<slug>`: each manifest's `limits.timeout_s` < `deadline_s − answer_reserve_s`.
  - `tool-in-capability:<slug>:<tool>`: for each tool a manifest declares, the tool's bound < the capability's `limits.timeout_s`. An MCP tool's bound is its broker bound (2 × `timeout_s` + `_BROKER_MARGIN_S`); an in-process tool's is its ToolSpec `timeout_s`. A declared tool with no bound found fails rather than drops out.
  - `facade-search-in-tool:<slug>:<tool>`: for each in-process tool of a manifest that declares `documents` (the broker hands it the facade, ADR-0231), one query's embedding plus rerank < the tool's bound.
  - `document-server-in-tool:<slug>:<tool>`: for the same tools, the server named by config/docs.yaml `mcp_server` has a `timeout_s` < the tool's bound.
  - `slot-and-attempt-in-broker:<server>`: one per config/mcp.yaml server.

  `generated(values)` lists the names these must produce, which `verify` expects alongside EXPECTED. Each family has one generic mutation (`mutations(values)`) that sets the value exactly at its bound, so the strict rule must refuse it. EXPECTED and MUTATIONS hold the fixed rules only. The `helper-in-shortest-capability` mutation sets the router to the shortest capability's limit. The rules stay strict (<), as CLAUDE.md rule 12c has it, where the review's sketch wrote ≤.
- Consequences:
  - The rules replace `docs-search-in-docs`, `embed-rerank-in-docs-search`, `server-call-in-docs-search:rag` and `broker-in-capability:routing:directions` under generated names. That last one is now one rule per tool: `routing.geocode` and `routing.route`.
  - `timeout budget 32 / 0 / 0`, self-test 32 / 0 / 0 (ADR-0269 had 31).
  - Tested (scripts/tests/test_timeout_budget.py, in `make test-budget`):
    - A test-only capability added at test time (an in-process tool, an MCP tool and the document facade) gets its five rules, all holding, each with a mutation that fails it, with no edit to budget.py.
    - Its tool past its limit fails its own rule.
    - Its tool with no bound fails rather than drops out.
    - Removing any one shipped capability, or all but one, fails nothing, and the self-test passes.
    - EXPECTED and MUTATIONS name no capability, tool or server.

### ADR-0276: The platform administrator manages the capacity and timeout settings from the web UI; the files are the defaults
- Date: 2026-10-22
- Phase: standalone PR before phase 06 (`feat/platform-settings`, owner decision 2026-10-22)
- Status: decided
- Context: Every capacity and timeout value lived in files (config/turn.yaml, config/models.yaml, config/docs.yaml, config/mcp.yaml), so any change was a redeploy, and nothing showed the timeout tree or what was in use. The owner decided that the platform administrator manages these values from the web UI, with the files as defaults, and that the same page shows the timeout tree and live usage, read-only (docs/PLATFORM-SETTINGS-PROMPT.md).
- Decision:
  - **Editable:**
    - config/turn.yaml: `per_user_max`, `per_tenant_max` (default none) with per-tenant overrides by tenant id, `deadline_s`, `answer_reserve_s`, `post_answer_reserve_s`, `evidence_fetch_s`.
    - config/models.yaml timeouts: helper, reasoning, embed_query, embed_batch, rerank.
    - config/docs.yaml: concurrent uploads per user and per tenant (with per-tenant overrides), uploads per minute per user, `register_response_s`.
    - config/mcp.yaml: `max_concurrency` per server, lowered only, never above the file's value (the ICD figure).
  - **Read-only, with their source and a note that a change needs a redeploy:** LOCK_POOL_SIZE, the ingest and mcp-rag-ref container memory limits, the sandbox child limits, `gateway_margin_s`, the keep-alive values.
  - **Storage.** The files stay the defaults, validated at startup as before. Overrides are versions of one document in `agent.platform_settings` (migration 0014), in the guard policies' pattern:
    - A save, a revert and a restore each insert the next version, and the newest is in force. Nothing is updated or deleted; a trigger refuses both (ADR-0166).
    - Version 1 is written by the seed with no overrides, so the history starts at the files' values.
    - Each version keeps its actor's id, username and display name (ADR-0123), and a revert names the version it brought back.
    - The agent caches the version in force in memory and in Redis, and reloads it on a pub/sub notice, on resubscribing and every 30 s. Every process reads it: the agent and the ingest service.
  - **A change applies to requests that start after it**; a request in flight keeps its values (ADR-0279). Hot paths read the cached snapshot, never the database. When Redis or Postgres cannot be read, the last known values stay in force.
  - **Access:** the `admin` role only (ADR-0117), checked at the gateway and at the agent (ADR-0284). Every call is audited fail-closed at the gateway. A change's record holds each value it changed, before and after, and the actor's username and display name.
  - **The gateway** reads the values it enforces (the turn deadline for its relay bound, uploads per minute, uploads in flight per user) from an internal agent endpoint, cached at most 10 s (ADR-0283).
  - **API:** agent and gateway 2.4.0, additive.
- Consequences: A value can now differ from its file. The settings page says which values are overridden and shows the default beside each one; docs/RESILIENCE.md says where to read the values in force. The reference MCP servers (infra/mcp-ref) stand for external servers and keep their file values. The details filled in are ADR-0277 to ADR-0285.

### ADR-0277: One loader builds the timeout budget's values for the script and the agent; the redeploy-only values are copied into config/deploy-values.yaml
- Date: 2026-10-22
- Phase: standalone PR before phase 06 (`feat/platform-settings`)
- Status: assumed — Accepted on 2026-10-24
- Context: Item 4 has the save use the same validator as scripts/check_timeout_budget.py. The validator's rules read values the agent's container cannot see: the gateway's application.yml (response-timeout, keep-alive, Tomcat's upload timeout), the agent's Dockerfile and the dev overlay (uvicorn keep-alive), docker-compose.yml (memory limits), infra/litellm/config.yaml (request_timeout, num_retries) and the reference RAG server's WORKERS. Item 2 also shows several of them read-only.
- Decision:
  - The loader moves from the script to services/agent/app/resilience/budget_values.py (PyYAML and the standard library, so the script still imports it). It builds the values from config/, the capability manifests and tool modules under app/, and the agent modules whose margins the rules check.
  - The redeploy-only values come from config/deploy-values.yaml, which `uv run scripts/check_timeout_budget.py --write-deploy-values` generates from those files, with each value's source.
  - The script reads them from the files themselves and fails `make test` when config/deploy-values.yaml differs from them (`deploy-values` line).
  - A test asserts that the agent's values and the script's are the same dict.
  - The agent writes into those values what its environment sets instead: the MCP allowlist it runs (MCP_CONFIG), UPLOADS_PER_MINUTE (`DOCS_UPLOADS_PER_MINUTE`, the gateway's own override), and LOCK_POOL_SIZE, which bounds the count ranges.
  - At startup it logs an error when the files themselves fail a rule, rather than refusing to start: `make test` is the gate, and every save is refused meanwhile.
- Alternatives:
  - Mounting the gateway's application.yml, docker-compose.yml and the LiteLLM config into the agent's container: rejected. It couples the agent to other services' files, and the agent does not run the dev overlay.
  - The gateway sending its values on each save: rejected. It leaves the agent's check dependent on a caller's claim, and LiteLLM's and compose's values are not the gateway's.
- Consequences: Changing one of those files now also means regenerating config/deploy-values.yaml; `make test` says so. scripts/tests gains two tests (drift caught, same values), 8 → 10.

### ADR-0278: Each editable setting has a range; a refusal names each conflict in plain Arabic and English words
- Date: 2026-10-22
- Phase: standalone PR before phase 06 (`feat/platform-settings`)
- Status: assumed — Accepted on 2026-10-24
- Context: Item 4 requires ranges (positive, sane maximums) next to the nested pairs, and a refusal message naming the conflict in plain words.
- Decision:
  - **Ranges.** budget.py holds the catalog of editable settings (`editable`), each with its range:
    - The turn and tenant counts: 1 to LOCK_POOL_SIZE.
    - Upload counts: 1 to `max_concurrent_uploads_total`. Uploads per minute: 1 to 1000.
    - `deadline_s`, `answer_reserve_s`, `post_answer_reserve_s`: 1 to 600 s. `evidence_fetch_s`: 1 to 120 s.
    - Helper and rerank ceilings: 1 to 120 s. Reasoning and the embedding batch: 1 to 600 s. A query embedding: 1 to 120 s.
    - `register_response_s`: 1 to 55 s (DocsConfig's own bound).
    - A server's max_concurrency: 1 to the file's value.
    - Counts are whole numbers. `per_tenant_max` and the upload tenant share may be null (no limit of their own). A tenant map's keys must be tenant ids.
  - **Rules.** `evaluate(values, overrides)` checks the ranges first, then every rule of `checks` on the values the overrides produce, the rules generated per manifest, tool and server included. An expected rule that goes missing is a problem too.
  - **Messages.** config/messages/settings.yaml holds one sentence per fixed rule, per family of generated rules, per range problem and per field label, in Arabic and in English. They name a capability by its manifest's name and a tool by its label, never by an id inside an Arabic sentence, and carry the rule's two values.
  - **The refusal.** A refused save is 400 `settings_invalid`, its message_ar and message_en joining those sentences and its `details` naming each rule. The gateway relays the agent's sentences as they are. The web page shows them, and its own catalog titles each rule of the timeout tree; a web test checks that every rule and field of config/messages/settings.yaml has its title.
- Consequences:
  - The shared rules also constrain lowering. The answering model's ceiling stays above LiteLLM's 85 s backstop (`litellm-backstop-in-longest-call`), so `models.reasoning_s` cannot go below 86 s without a redeploy of infra/litellm/config.yaml.
  - The helper ceiling stays under the shortest capability's 30 s.
  - A `deadline_s` of 80 s fails the 60 s capabilities and the 90 s answering model at once.

### ADR-0279: A request pins the settings current at its start; a lowered shared limit applies to what starts after it
- Date: 2026-10-22
- Phase: standalone PR before phase 06 (`feat/platform-settings`)
- Status: assumed — Accepted on 2026-10-24
- Context: Item 3: a change applies to requests that start after it; requests in flight keep their values. The values are read in many places: the runner, the capability and answer nodes, the guards, the summary and memory steps, the model, embedding and rerank gateways, the upload service and the ingest worker.
- Decision:
  - **Pinning.** One immutable snapshot (`app/platform_settings/effective.py`) holds the overrides in force and what they change in each configuration object. A request pins the snapshot current when it starts, in a ContextVar, and the tasks it starts inherit it:
    - a turn at admission (`Admission.settings`, pinned again at the start of the turn's task);
    - an upload at registration (its background hand-over included);
    - an ingest job when the worker takes it.
  - **Reading.** Readers call `effective.turn(default)`, `effective.timeouts(default)` and `effective.register_response_s(default)`: the caller's own default with the pinned overrides written in, built once per default object, or the default itself when nothing is pinned. A test that swaps `services.turn` keeps its own configuration that way.
  - **Shared limits.** Counters shared across requests — the turn limiter, the registrations limiter, each MCP server's slots — are resized by a hook on every new snapshot. What is in flight keeps its slot, and a request that starts after the change meets the new limit. That is how per_user_max 2 → 1 refuses the next turn while the first goes on.
- Consequences: A turn admitted just before a change runs to its end with the old deadline. A change does not reach a request already running; the relay's bound at the gateway follows the deadline in force when the stream starts (ADR-0283).

### ADR-0280: A stored version that no longer passes the rules is ignored as a whole
- Date: 2026-10-22
- Phase: standalone PR before phase 06 (`feat/platform-settings`)
- Status: assumed — Accepted on 2026-10-24
- Context: Item 3: a stored override that fails validation at startup (for example after a file default changed) is ignored, the file value used, and an error logged. Rules span several values, so one value's override cannot always be judged alone.
- Decision: Every load of the version in force (start, notice, refresh) checks its overrides with `evaluate` against the files' values. When any problem is found, the whole version is ignored: the files' values are in force, an error is logged with the rules it fails, and the page says so (`ignored`). The version stays in the history, and the next save or revert replaces it.
- Consequences: One stale value voids the other overrides of its version until the administrator saves again. Dropping only the values a failing rule names would need each rule to say which of its inputs to drop; the simplest consistent option is the files' values.

### ADR-0281: An MCP server's max_concurrency is lowered by holding back permits of its own semaphore
- Date: 2026-10-22
- Phase: standalone PR before phase 06 (`feat/platform-settings`)
- Status: assumed — Accepted on 2026-10-24
- Context: Item 1: a server's max_concurrency may be lowered from the page, never raised above the file's value, which is the figure agreed in the ICD. The client sizes an `asyncio.Semaphore` (and the background half, ADR-0265) at startup. asyncio's semaphore cannot be resized. Replacing it while calls hold the old one would let old holders and new callers together pass the file's value.
- Decision: The semaphore stays asyncio's, so its waiting, FIFO fairness and cancellation stay the library's. A `_Holdback` (app/mcp/client.py) keeps `file − limit` of its permits out of use:
  - It takes the permits free now at once, and the rest in the semaphore's own queue as calls return theirs.
  - A call holding a slot keeps it; one waiting is served after the holdback.
  - Raising the limit back returns the held permits.
  - The background semaphore is held back to half of the new limit, at least 1.
  - In flight never passes max(old, new) ≤ the file's value.
  - The server's `in_use` and `background_in_use` are counted for the usage section.
- Alternatives:
  - A custom resizable semaphore: rejected. It rewrites waiting and cancellation, which the library gets right.
  - Replacing the semaphore: rejected; it can pass the ICD figure.
  - aiolimiter: rejected. It limits a rate, not what is in flight.
  - Envoy or a proxy limit: rejected. It is per host, not per server entry, and does not exist in compose.
- Consequences: Lowering takes effect as calls end, never by cancelling them. A row in docs/RESILIENCE.md records it.

### ADR-0282: The usage section reads the process's counters only; counts, never who
- Date: 2026-10-22
- Phase: standalone PR before phase 06 (`feat/platform-settings`)
- Status: assumed — Accepted on 2026-10-24
- Context: Item 7b: usage from in-memory counters only, no database and no per-request cost, counts only.
- Decision: `GET /v1/admin/usage` (agent, admin only) answers:
  - turns in flight (the platform, each tenant with something in flight, the busiest user's count) against their limits, from `ConcurrencyLimiter.snapshot`;
  - per MCP server, slots and background slots in use against the limits in force, and the breaker's state with the seconds since it opened;
  - registrations still running after their upload answered `pending`;
  - `task_abandoned` since start (`deadline.ABANDONED`).
  
  No user id or name appears. Tenant ids appear, as chips on the page. The gateway proxies and audits each read like every admin call (ADR-0042). The page refreshes it every 5 s while it is open and visible.
- Consequences: The audit log gains one `settings.usage` row per open page every 5 s. Measured: with 20 turns in flight, the endpoint answered in 1.1 ms at worst (median 0.3 ms), under the 50 ms bound (tests/integration/test_platform_settings.py).

### ADR-0283: The gateway reads the values it enforces every 5 s, each read bounded by 4 s; the last values read stay in force
- Date: 2026-10-22
- Phase: standalone PR before phase 06 (`feat/platform-settings`)
- Status: assumed — Accepted on 2026-10-24
- Context: Item 3: the gateway enforces some of the values itself, so it reads them from an internal agent endpoint, cached at most 10 s. The values are the turn deadline (its relay bound), uploads per minute and uploads in flight per user.
- Decision:
  - `PlatformLimits` (gateway) reads `GET /v1/internal/settings/limits` (internal token, no caller) every `gateway.settings.refresh`, 5 s, on a thread of its own. Each read is bounded 4 s from request to body, so a change reaches the gateway within 10 s.
  - Hot paths read its cache only. The upload rate limiter and the per-user upload cap read it per upload; the relay bound is read when each turn's stream is sent.
  - An unreachable or unusable answer keeps the last values. Before the first read, the files' values apply (with GATEWAY_DOCS_UPLOADS_PER_MINUTE).
  - The test profile sets the refresh to 0 (off), since its mock agent answers in order; PlatformLimitsTest reads on its own.
  - `gateway_headers_s` and `gateway_margin_s` stay the file's: they are redeploy-only.
- Consequences: The gateway-wide upload cap (`max_concurrent_uploads_total`) stays the file's. One small request every 5 s reaches the agent.

### ADR-0284: The agent checks the admin role too, from the gateway's X-User-Admin on admin calls
- Date: 2026-10-22
- Phase: standalone PR before phase 06 (`feat/platform-settings`)
- Status: assumed — Accepted on 2026-10-24
- Context: Item 5: admin only, checked at the gateway and the agent. The agent is role-blind (ADR-0046); its admin policy API trusts the internal token, and the gateway alone checks the role.
- Decision: The gateway's admin proxy (`AgentClient.exchangeAdmin`, used by the policy and settings controllers) sends `X-User-Admin`, `true` or `false`, read from the same verified token as X-User-Id. Every settings and usage endpoint of the agent refuses a call without `true` (403 `forbidden`, new in config/messages/errors.yaml), after the internal token. A gateway route that forwarded a user's call there by mistake is still refused. The agent's policy endpoints are unchanged.
- Consequences: Defence in depth within the compose network's trust (the internal token): it does not protect against a forged internal call, which the network policy and the token do. The internal limits endpoint the gateway polls has no caller and needs no role.

### ADR-0285: The details this PR filled in
- Date: 2026-10-22
- Phase: standalone PR before phase 06 (`feat/platform-settings`)
- Status: assumed — Accepted on 2026-10-24
- Context: Details the owner's decision left open.
- Decision:
  1. The models.yaml "helper" timeout sets the router, summarizer, judge and extractor ceilings together, and "reasoning" sets reasoning and reflection. Each default is the longest of its roles.
  2. The settings keys are dotted ids (`turn.per_user_max`, `models.helper_s`, `docs.register_response_s`, `mcp.<server>.max_concurrency`), shown in chips only.
  3. The per-tenant upload map (`limits.tenant_concurrent_uploads`) is editable beside the tenant share, as the turn overrides are.
  4. A save whose overrides are a subset of those in force, each unchanged, is recorded as `restore`. A save may name the version it was made from (`expected_version`); another save first is 409 `settings_conflict`.
  5. A revert of a version that no longer passes the rules is refused like a save.
  6. The page's save goes through a review step: a preview (`POST /v1/admin/settings/preview`) shows what changes, each conflict and the timeout tree on the draft's values, and only a valid preview of the current draft can be confirmed.
  7. Uploads per minute's default is the deployment's UPLOADS_PER_MINUTE where set (the gateway's own override), else config/docs.yaml's.
- Consequences: The owner may prefer separate helper roles, per-key restore rows, or a save without the review step.

### ADR-0286: The timeout tree nests each pair under the pair whose inner bound is its outer one
- Date: 2026-10-24
- Phase: standalone PR before phase 06 (`feat/platform-settings`), the owner's visual check, item 4
- Status: decided
- Context: The owner's check: the timeout tree must be a tree, each inner bound indented under its outer one (turn → capability → tool → MCP server, gateway stream → turn), with a warning mark on the ancestors of a failing row. The page showed a flat list. The rules in `app/resilience/budget.py` already link their pairs by name: one pair's outer bound is another pair's inner bound.
- Decision:
  1. `budget.parents(values, checks)` gives each timeout pair the pairs whose `inner` equals its `outer`. Three outer bounds are derived from another bound and resolve to it: the room before the answer (`deadline_s − answer_reserve_s`) to `deadline_s`; the longest role ceiling to that role's ceiling; the shortest capability's limit to that capability's limit. The slot-and-attempt rule's broker bound is matched to the tool rules' name for the same bound.
  2. A pair of one capability nests only under a pair of the same capability. An MCP server's pair nests under each tool on that server, so it shows under both of a capability's two tools. A pair with no parent is a root.
  3. `TimeoutPair` gains `parents` (agent and gateway API 2.4.0, additive; the version has not left this PR, ADR-0046). The page indents each row by its depth and keeps the pass or fail mark per row. A row with a failing pair anywhere under it also shows "تعارض تحته" / "Conflict below".
- Consequences: On the shipped configuration the roots are the gateway's relay (turn, answer reserve, the model call, each capability with its tools and servers), the header bound, the upload pairs, the keep-alive pairs and the document server's broker pair. A new rule nests with no page change when its outer label is another rule's inner label. A new derived bound needs a line in `parents`, or its pair shows as a root.

### ADR-0287: A usage read is audited when it is a page session's first, then at most once per 10 minutes per admin
- Date: 2026-10-24
- Phase: standalone PR before phase 06 (`feat/platform-settings`), the owner's visual check, item 5
- Status: decided
- Context: ADR-0282 audited every usage read. The page reads every 5 s, so one open page wrote 720 audit rows an hour. The owner's check: audit the first read of a page session, then at most one row per 10 minutes per admin. Settings changes stay audited on every save.
- Decision:
  1. The page sends `page_session` on `GET /v1/admin/usage`: a random id it makes once per page load (`crypto.randomUUID`), letters, digits and hyphens, at most 64 characters. Any other value is refused with 400 `invalid_request` before the agent is called. The id is recorded in the row's detail.
  2. The gateway's `UsageAuditGate` writes an OK row when the read's page session is new for this admin, or when the admin's last row is 10 minutes old. A read without a page session is due by the interval only. The gate holds at most 1024 admins, each with its 32 latest page sessions, in memory. A row that fails to write is due again on the next read. The read is then answered `audit_unavailable`, as before.
  3. Unchanged: the audit log's reachability is checked before every read, so reads stay fail-closed. A refused read (not an admin, the agent unavailable) is audited every time. Every settings call other than usage (read, preview, save, revert, versions) is audited on every call.
- Consequences: An admin with the page open writes one usage row on load and then at most six an hour, however many reads. The gate is per gateway instance, so with more than one instance each writes its own rows. This amends ADR-0282's sentence on auditing; the rest of ADR-0282 stands.

### ADR-0288: The settings page writes numbers, dates and times in Western digits in both languages
- Date: 2026-10-24
- Phase: standalone PR before phase 06 (`feat/platform-settings`), the owner's visual check, item 1
- Status: decided
- Context: The page wrote numbers in Arabic-Indic digits in Arabic (the `ar-SA` locale). The agent's refusal sentences followed it (commit 464d256). The owner's check: Western digits (0–9) everywhere on `/admin/settings`, in both languages, inputs and text alike.
- Decision: The page has one formatter, `pageFormat(locale)` in `services/web/src/lib/settings.ts`. It formats numbers and times with `ar-SA-u-nu-latn` in Arabic and `en-GB` in English, without digit grouping. A digit typed in Arabic-Indic or Persian form becomes its Western digit in the field. The catalog's help lines on the page use Western digits. The agent's Arabic refusal sentences write their numbers in Western digits again (464d256 reverted).
- Consequences: Other pages keep their own digit choices. Dates in Arabic keep the `ar-SA` calendar, with Western digits.
