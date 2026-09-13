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
- Status: assumed — Accepted on 2026-09-13
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
- Status: assumed — Accepted on 2026-09-13
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
- Status: proposed
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
