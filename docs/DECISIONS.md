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
- Status: decided
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
- Status: assumed
- Context: README explained the architecture but not how to start from an empty machine, and every model endpoint was assumed to be Ollama on the developer's own host.
- Decision:
  - README §21 documents prerequisites, the exact first-run commands, URLs and seed users, and a subsection on pointing LiteLLM at a shared model server.
  - `scripts/pull_models.sh` (target `make models`) pulls the models named in `infra/litellm/config.yaml` — still the only file that names a model — through the Ollama HTTP API, so `OLLAMA_HOST` can point it at a remote server. Present models are skipped, so it is re-runnable.
  - `VLLM_API_KEY` is added to `.env.example` and passed to the `litellm` container, so moving a tier to an OpenAI-compatible endpoint is two `.env` values plus three lines in the LiteLLM config.
- Consequences: The host-side scripts (`preflight.sh`, `pull_models.sh`) read shell variables, not `.env`, because they run outside compose; §21.4 says so explicitly. `make models` needs `curl` and `jq`, which `preflight.sh` already assumes.

### ADR-0030: Evaluate `qwen2.5:7b-instruct` as the `fast` model, on helper-role work
- Date: 2026-09-13
- Phase: 03 (proposed by the owner while deciding ADR-0024)
- Status: proposed
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
- Status: decided
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
- Status: decided
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
