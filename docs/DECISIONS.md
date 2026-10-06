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
- Status: decided — Superseded by ADR-0048 on 2026-09-13 (what the history stores only)
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
- Status: decided — Superseded by ADR-0049 on 2026-09-13 (ceiling arithmetic and the max_tier type only)
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
