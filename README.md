# Sovereign Agent Platform — Build Specification

> **Audience:** this file is written for Claude Code. It is the single source of truth for what to build, in what order, and which decisions are already made. Do not re-open closed decisions (see §2). Where the spec is silent, choose the simplest option that keeps the stated architecture intact and record the choice in `docs/DECISIONS.md`.
>
> **Language:** all code, identifiers, commits and docs are English. The end-user UI is Arabic-first with full RTL, and every user-facing string must live in an i18n file (`ar` default, `en` secondary).

---

## 0. One-paragraph summary

A production-grade, self-hostable AI agent platform that runs on an air-gapped or restricted network. One LangGraph-based agent serves five use cases (Text-to-SQL over PostgreSQL, web search, chat-with-uploaded-documents, search across admin-curated knowledge bases, and general knowledge answers) behind a Keycloak-secured web UI, with a **dynamic, admin-configurable guardrails engine** enforced at seven checkpoints, structured **state** separate from long-term **memory**, an explicit **context assembly** step before every model call, **reflection** for high-value turns, and **observability + evaluation** wired in from day one. Everything runs from a single `docker compose up`.

The governing principle (from the architecture explorer this project is based on):

> Not every request passes through every component. A good production architecture activates the right components for the task, the risk and the context.

---

## 1. Use cases (all five are in scope for v1)

| # | Use case | Trigger | Key components |
|---|----------|---------|----------------|
| UC1 | **Text-to-SQL** over PostgreSQL business tables | user asks a quantitative question; `sql` tool enabled for user | schema retrieval, SQL generation, static validation, read-only execution, self-correction loop, result interpretation |
| UC2 | **Web search** | user asks for current/external info; `web_search` tool enabled | SearXNG (self-hosted), fetch + sanitize, citation validation |
| UC3 | **Chat with uploaded documents** (session-scoped) | user attaches files in the chat | ingestion pipeline → session-scoped vector collection → retrieval per question |
| UC4 | **Knowledge-base plugins** (org-scoped, admin-curated) | user toggles one or more KB "plugins" next to the chat bar (e.g. *Ministry of Education KB*, *Ministry of Health KB*) | per-KB vector collections, Keycloak-enforced access, retrieval only from selected + permitted KBs |
| UC5 | **General knowledge** | none of the above applies | direct model answer, no tools, minimal context |

A single message may combine use cases (e.g. UC1 + UC4). The orchestrator decides which nodes run; the user never picks a "mode" except for KB plugins and file uploads.

---

## 2. Decisions already made (do not revisit)

| Area | Decision | Why (and what it costs) |
|------|----------|-------------------------|
| Agent orchestration | **LangGraph** (Python 3.12) | Mature, explicit graph/state model, checkpointing, human-in-the-loop interrupts, native streaming, largest ecosystem. Cost: Python service alongside the JVM gateway; graph design is upfront work. Rejected: CrewAI/AutoGen (too autonomous for gov use), Semantic Kernel (weaker graph semantics), Spring AI alone (no durable graph). |
| Architecture style | **Workflow-First Agent** | Code owns the control flow; the model reasons inside bounded nodes. This is the middle option of the four in the explorer and the right default for regulated environments. |
| API gateway / AuthZ / Audit | **Spring Boot 3 (Java 21)** as BFF | Keycloak validation, fine-grained authorization, audit log, admin APIs, streaming proxy to the agent service. Keeps Java where the org's authz expertise is. Cost: two backends; mitigated by a small, versioned HTTP contract (§6.2). *If a faster MVP is needed, the gateway may be a FastAPI service with the same contract — but the two-service split stays.* |
| Model serving | **OpenAI-compatible endpoint** behind **LiteLLM** | Dev: Ollama or vLLM on the developer's machine. Prod: vLLM. The agent never talks to a model directly; it talks to LiteLLM. |
| Models (dev defaults) | Three **tiers**: `fast` = `qwen2.5:3b`, `standard` = `qwen2.5:14b-instruct`, `heavy` = `qwen3:32b` (or the largest model the host can run); Embeddings: `bge-m3`; Reranker: `bge-reranker-v2-m3` | All open-weight, Arabic-capable, runnable locally. **Code never names a model; it names a role, and config maps roles to tiers (§19).** |
| Vector store | **pgvector** in the same PostgreSQL, behind a `VectorStore` interface | Fewest moving parts for compose; per-KB isolation via separate tables/namespaces. Production may swap to Milvus/Qdrant by implementing the interface — no other code changes. |
| State store | **PostgreSQL** (LangGraph Postgres checkpointer + app tables) | Durable, queryable, survives restarts. Redis is used only for rate limits, short TTL caches and pub/sub for streaming. |
| Memory store | PostgreSQL tables + pgvector embeddings | Custom implementation (§8). Rejected for v1: Mem0/Letta — policy over "what is worth remembering" must stay in our code. |
| Guardrails | **Custom policy engine** (§10) + pluggable checkers: regex/Presidio (PII), LLM-judge (topics, Arabic-capable), allow/deny lists, OPA (tool authorization) | Existing guard models have weak Arabic coverage; the LLM-judge checker with Arabic prompts is the primary topic filter. |
| Observability | **OpenTelemetry → Langfuse** (traces, tokens, cost, datasets, scores) | One trace per user message; spans per node/tool/model call. |
| Evaluation | **Langfuse datasets + scores**, **Ragas** for RAG metrics, custom SQL golden-set runner | Offline (CI) and online (sampled LLM-judge) evaluation. |
| Identity | **Keycloak** (OIDC) with realm roles + KB permissions modelled as roles | See §11. |
| Web search | **SearXNG** self-hosted | Works on restricted networks (can point at internal mirrors); no third-party API keys. |
| Object storage | **MinIO** | Uploads, ingested artifacts, exports. |
| Frontend | **Next.js 15 + TypeScript + Tailwind**, `next-intl`, RTL by default | Chat, KB plugin toggles, uploads, admin console. |
| Data access (UC1) | PostgreSQL only in v1; the SQL tool is written against a `QueryEngine` interface so **StarRocks/Trino over HDFS** can be added in v2 | Semantic layer (YAML metric/table descriptions) is mandatory from v1. |

> **Amendment 2026-10-05 (ADR-0065).** *Model serving:* for **development only**, the chat tiers may be served by a hosted OpenAI-compatible endpoint (OpenRouter, with open-weight models of the kind production can host), selected in `.env` (§21.4, ADR-0066). No real or ministry data in development while it is selected. Production stays offline: vLLM behind LiteLLM, no external SaaS (§16).

---

## 3. Target architecture

```
                         ┌──────────────────────────────────────────────────────────┐
                         │  Cross-cutting (always on)                               │
                         │  Guardrails/Policy Engine · Secrets · OTel/Langfuse ·    │
                         │  Evaluation · Audit log · Platform (compose/k8s)         │
                         └──────────────────────────────────────────────────────────┘

 Browser ──HTTPS──▶ Next.js UI ──▶ Spring Boot Gateway (BFF) ──HTTP/SSE──▶ Agent Service (LangGraph)
                    (Keycloak     · verifies JWT                              │
                     PKCE login)  · authorizes (roles, KB perms)              │ per turn:
                                  · audit log                                 │  load State ─▶ Context Assembly ─▶ Router
                                  · admin APIs (policies, KBs, perms)         │  ─▶ [nodes: sql | web | docs | kb | answer]
                                  · streams tokens/events back                │  ─▶ Reflection? ─▶ Validation ─▶ Output guard
                                                                              │  ─▶ save State ─▶ (async) Memory write + Eval
                                                                              ▼
                     ┌────────────┬────────────┬──────────────┬───────────────┬─────────────┐
                     │ LiteLLM    │ PostgreSQL │ Redis        │ SearXNG       │ MinIO       │
                     │ → vLLM /   │ + pgvector │ (cache, rate │ (web search)  │ (uploads)   │
                     │   Ollama   │ (state,    │  limit, pub/ │               │             │
                     │            │  memory,   │  sub)        │               │             │
                     │            │  KBs,      │              │               │             │
                     │            │  business  │              │               │             │
                     │            │  data)     │              │               │             │
                     └────────────┴────────────┴──────────────┴───────────────┴─────────────┘
                     ┌────────────┬────────────┬──────────────┐
                     │ Keycloak   │ Langfuse   │ Presidio     │
                     │ (OIDC)     │ (+CH,MinIO)│ (PII)        │
                     └────────────┴────────────┴──────────────┘
```

### 3.1 Component tiers (mirror the explorer's map)

- **Tier 1 — every message:** Channels (UI/gateway), Identity & Session, Context Assembly, Orchestrator, Reasoning Model (via Gateway), State, Memory (read), Validation.
- **Tier 2 — on demand:** Planning, Tool Selection/Execution, Web Search, RAG/Retrieval (docs & KBs), SQL tool, Reflection, Human-in-the-loop (only for write-class tools; none exist in v1, but the interrupt path must exist and be tested).
- **Tier 3 — cross-cutting:** Guardrails, Secrets & Authorization, Observability, Evaluation, Audit, Platform.

---

## 4. Repository layout

```
.
├── README.md                    ← this file
├── docker-compose.yml
├── docker-compose.dev.yml       ← hot reload, exposed ports
├── .env.example
├── Makefile                     ← make up / down / seed / eval / test / logs
├── docs/
│   ├── DECISIONS.md             ← ADR-style log; append, never rewrite
│   ├── CONTRACT.md              ← gateway ⇄ agent HTTP/SSE contract (generated from OpenAPI)
│   └── RUNBOOK.md
├── services/
│   ├── agent/                   ← Python 3.12, LangGraph, FastAPI (internal only)
│   │   ├── app/
│   │   │   ├── graph/           ← nodes/, edges.py, state.py, build.py
│   │   │   ├── context/         ← assembler.py, budget.py, summarizer.py, selectors/
│   │   │   ├── memory/          ← store.py, writer.py, retriever.py, policy.py
│   │   │   ├── capabilities/    ← ONE package per capability (§18): sql/, web/, docs/, kb/, general/, email_proofreader/
│   │   │   ├── tools/           ← registry.py, broker.py (shared execution path; no capability code here)
│   │   │   ├── guardrails/      ← engine.py, checkers/, checkpoints.py, schema.py
│   │   │   ├── rag/             ← shared core library used by capabilities: ingest/, chunkers/, embed.py, vectorstore/, rerank.py
│   │   │   ├── models/          ← gateway.py (LiteLLM client), routing.py, prompts.py (loader only; prompt files live in config/prompts/ and capabilities/*/prompts/)
│   │   │   ├── observability/   ← otel.py, langfuse.py
│   │   │   ├── evaluation/      ← online_judge.py
│   │   │   └── api/             ← FastAPI routes (internal)
│   │   ├── tests/
│   │   └── pyproject.toml       ← uv-managed
│   ├── gateway/                 ← Spring Boot 3, Java 21, Gradle
│   │   └── src/main/java/sa/ncai/agent/gateway/{auth,authz,audit,admin,chat,proxy}
│   └── web/                     ← Next.js 15
│       └── src/{app,components,i18n,lib}
├── infra/
│   ├── keycloak/realm-export.json
│   ├── litellm/config.yaml
│   ├── searxng/settings.yml
│   ├── otel/collector.yaml
│   ├── presidio/
│   └── postgres/init/           ← extensions, schemas, seed business data for UC1
├── config/
│   ├── policies/                ← guardrail policies (YAML), seeded into DB on first run
│   ├── semantic-layer/          ← tables.yaml, metrics.yaml, examples.yaml (UC1)
│   └── prompts/                 ← versioned prompt files
├── evals/
│   ├── golden/                  ← sql.jsonl, rag.jsonl, guardrails.jsonl, routing.jsonl
│   └── run_evals.py
└── scripts/                     ← seed_kb.py, create_users.sh, smoke.sh
```

---

## 5. Agent service (LangGraph)

### 5.1 State schema (single source of truth — `app/graph/state.py`)

State is **structured data read by code**, checkpointed by LangGraph's Postgres checkpointer per `thread_id = conversation_id`. Rule: a field belongs in State only if some code reads it and acts on it.

```python
class Attachment(TypedDict):
    id: str; name: str; mime: str; size: int
    collection: str            # session-scoped vector collection name
    status: Literal["pending","indexed","failed"]

class ToolCall(TypedDict):
    id: str; tool: str; args: dict; status: Literal["ok","error","denied","pending_approval"]
    latency_ms: int; retries: int; error: str | None

class Decision(TypedDict):
    key: str; value: str; reason: str; turn: int

class AgentState(TypedDict):
    # identity (set by gateway, never by the model)
    conversation_id: str
    user_id: str
    tenant_id: str
    permissions: list[str]              # e.g. ["cap:general","cap:sql","kb:moe:read"]
    # per-turn
    turn: int
    user_message: str
    attachments: list[Attachment]
    selected_kbs: list[str]             # KB slugs toggled in the UI for this turn
    intent: dict                        # router output {intent, needs_tools, risk, complexity}
    plan: list[str] | None
    iteration: int                      # loop counter inside tool nodes
    tool_calls: list[ToolCall]
    retrieved: list[dict]               # chunks with {source, score, text, kb}
    sql_history: list[dict]             # {sql, ok, error, rows_preview}
    draft_answer: str | None
    citations: list[dict]
    guard_events: list[dict]            # {checkpoint, policy, action, detail}
    reflection: dict | None             # {passed, issues[], retry_hint}
    # across turns
    summary: str | None                 # rolling summary of older turns
    decisions: list[Decision]
    open_threads: list[str]
    artifacts: list[dict]
    # accounting
    tokens_in: int; tokens_out: int; cost_usd: float
    messages: Annotated[list, add_messages]   # LangGraph message history (raw, trimmed by Context Assembly, never sent whole)
```

### 5.2 Graph (capability-agnostic — see §18)

The main graph has **no capability-specific branches**. Capabilities (SQL, web, docs, KB, general, and any future plugin such as an email proofreader) are packages discovered at startup and executed through one generic node.

```
START
 └─▶ load_identity_and_state         (from gateway headers + checkpointer)
 └─▶ guard(INPUT)                    ← checkpoint 1
 └─▶ ingest_attachments?             (if new attachments)
 └─▶ assemble_context                (§7; also computes budget)
 └─▶ route                           (rules from manifests → router model; output: ordered list of capability slugs, max 3)
 └─▶ guard(TOOL_LIST)                ← checkpoint 2: filters selected capabilities by permissions + policies
 └─▶ run_capabilities                (for each selected slug: invoke its compiled subgraph with CapabilityInput; collect CapabilityOutput)
 └─▶ answer                          (reasoning model synthesizes outputs; structured {answer, citations})
 └─▶ validate                        (deterministic: citations ⊆ sources, schema, length)
 └─▶ reflect?                        (rules: risk≥medium OR complexity≥high OR validate failed once; max 1 retry)
 └─▶ guard(OUTPUT) + guard(DATA)     ← checkpoints 5, 6
 └─▶ persist_state
 └─▶ END  → async: memory_writer, online_eval, audit_flush
```

Loop behaviour (`reason → tool_decision → guard(PRE_TOOL) → execute → guard(POST_TOOL) → observe`) lives **inside** capability subgraphs that need it (web, sql), using the shared `tools/broker.py` and the shared `loop_limits` config. The core never knows a loop exists.

**Human-in-the-loop:** LangGraph `interrupt()` inside the broker before any tool whose policy returns `require_approval`. No v1 tool triggers it by default, but `scripts/smoke.sh` must exercise it.

### 5.3 Routing (who decides what)

| Question | Decided by | Implementation |
|----------|------------|----------------|
| Is a capability even available? | **Permissions + policies** | `cap:{slug}` role in JWT and `TOOL_LIST` policies filter the registry before routing |
| Which capabilities apply? | **Manifest rules → Router model** | Rules from each manifest's `routing_hints.rules` (attachments present, capability toggled in UI, keyword match). If undecided, the 3B router model receives the *list of available capabilities with their `routing_hints.description`* and returns JSON `{capabilities: [slug...], complexity, risk}`. Adding a capability changes this list, not this code. Cache by hash for 1h. |
| Which query / SQL / collection? | **Reasoning model inside the subgraph** | Bounded by structured output |
| Which model tier? | **Tier resolver** (`models/routing.py`, §19) | max of: capability `min_tier`, router escalation (complexity/risk), Context Assembly escalation (context does not fit), validation-failure escalation (once) — capped by policy (`MODEL_TIER` checkpoint) |
| Run reflection? | **Rules** | see 5.2 |
| Human approval? | **Policy engine only** | never the model |
| Loop done? | **Broker / subgraph orchestrator** | model says final OR shared limits reached OR validation failed twice |

> **Amendment 2026-10-15 (phase 05, ADR-0216).** A manifest rule `keyword_candidate` makes a capability a router-model candidate when one of its keywords is in the message; it never selects it. The router model then chooses among the candidates and the fallback capabilities, as for `conversation_attachments`; a turn with no candidate costs no router call, and the router's cache key is unchanged (ADR-0169).

## 6. Gateway (Spring Boot)

### 6.1 Responsibilities

1. OIDC resource server: validate Keycloak JWT (issuer, audience, expiry, signature via JWKS).
2. Build the **principal context** sent to the agent: `user_id`, `tenant_id`, `permissions[]` (derived from realm roles, see §11), `display_name`, `locale`.
3. Authorization checks that must never depend on the agent: KB access on `/chat` (selected KBs ⊆ permitted), admin endpoints (`admin` role), upload size/type limits.
4. Audit log (append-only table `audit_events`): who, what, when, request id, tool calls summary, guard events, policy versions. The agent posts audit deltas back; the gateway writes them.
5. Conversation CRUD, upload to MinIO (pre-signed), KB management, policy management (§10.4), permission management (Keycloak Admin REST API).
6. Streaming proxy: forwards agent SSE events to the browser unchanged (token, tool_start, tool_end, guard, citation, done, error).

> **Amendment 2026-10-11 (ADR-0042, ADR-0116, ADR-0118).** The relay forwards every event unchanged except the terminal one: it rebuilds the terminal `done` or `error` event without `guard_audit` (the checker detail meant for the audit record only), and it drops an event that is incomplete when the upstream ends or breaks, sending `agent_stream_interrupted` instead.

> **Amendment 2026-10-11 (ADR-0129).** Uploads go through the gateway, not pre-signed URLs: the browser posts the file to the gateway, which checks the limits of `config/docs.yaml` and the conversation's ownership, stores the bytes in MinIO bucket `uploads` and registers the attachment with the agent. Upload, list, status, download and delete are audited fail-closed. MinIO stays internal.

### 6.2 Gateway ⇄ Agent contract (internal network only; no auth beyond mTLS/network policy in v1, shared secret header acceptable in compose)

```
POST /v1/turns              body: {conversation_id, principal, message, attachments[], selected_kbs[], locale}
                            → SSE stream of events; final event carries {answer, citations, usage, guard_events, state_digest}
POST /v1/turns/{id}/approve body: {approved: bool, note}      ← HITL resume
POST /v1/kb/{slug}/ingest   body: {object_key, metadata}      ← admin ingestion
GET  /v1/health
```
> **Amendment 2026-10-15 (phase 05, ADR-0209, ADR-0214, ADR-0219).** Agent and gateway API version 2.2.0, additive: `tool_start` carries the tool's `label {ar, en}`; an attachment carries `warnings` (`[{code: pages_without_text, count}]`, ADR-0201); the terminal `done` and `error` events carry `tool_audit`, every tool call of the turn without content, from the agent to the gateway only (the gateway writes it into the audit record as `tool_calls` and removes it, like `guard_audit`); `GET /v1/health` lists each MCP server and each disabled tool, content-free.

### 6.3 API standards

- **REST:** every HTTP API (gateway public API, agent internal API) is described by **OpenAPI 3.1**. FastAPI generates the agent spec from Pydantic models; the gateway uses `springdoc-openapi`. Specs are committed under `docs/openapi/{gateway,agent}.json` and regenerated in CI; a diff without a version bump fails the build.
- **Streaming events:** SSE payloads are not covered by OpenAPI, so the event envelope and every event type (`token, tool_start, tool_end, guard, citation, tier, done, error`) are defined once as Pydantic models in `services/agent/app/api/events.py`, exported as **JSON Schema** (`docs/events.schema.json`) and, optionally, an **AsyncAPI 3** document.
- **Client generation:** the frontend consumes generated TypeScript types (`openapi-typescript` for REST, `json-schema-to-typescript` for events); no hand-written API types in `services/web`.
- **Versioning:** URL prefix `/v1`; breaking changes bump the prefix. Envelope: `{request_id, data | error{code, message_ar, message_en}}`.
- `docs/CONTRACT.md` is generated from the specs above at build time.

---

## 7. Context Assembly (the most important component)

`app/context/assembler.py` runs **before every model call**, not once per turn. It is custom code; libraries only help count and retrieve.

Inputs: system prompt (versioned), `AgentState`, node-specific needs. Output: a `ContextPackage` with ordered blocks and a token accounting record (logged to the trace).

Pipeline (each step is a class with `apply(package, budget) -> package`; order is config):

1. **System instructions** — always. Includes the *permitted* tool list only, and a compact rendering of active policy hints (what is disallowed), never the full policies.
2. **State digest** — always. ~300 tokens: phase, decisions, open threads, artifacts, selected KBs.
3. **Sliding window** — last `N=6` messages raw.
4. **Rolling summary** — messages older than the window are summarized incrementally by the small model (`summary_old + new_messages → summary_new`). Stored in `state.summary`. Trigger: history > `8k` tokens.
5. **Memory retrieval** — top-`k=5` memory items by embedding similarity to the current message + intent; filtered by `user_id`/`tenant_id`.
6. **Semantic retrieval** — only for docs/kb/sql nodes: chunks from session collection and/or selected KBs; hybrid (BM25 via `tsvector` + vector), reranked, `top_k=8`, then **compressed** (extractive: keep sentences relevant to the question) if over budget.
7. **Tool results** — current turn only; sanitized (§10.3) and truncated to `max_tool_result_tokens`.
8. **Selective injection** — per node rules decide *form*: full / summary / reference. Example: `answer` node after `web_loop` gets the synthesized findings, not raw pages.
9. **Budget guard** — hard cap `context_budget = tier_window - reserved_output - reserved_loop_headroom`, where `tier_window` comes from the **provisional tier** (§19.3). If the required blocks do not fit, the assembler returns `NeedsMore{deficit_tokens}` and the tier resolver decides — per config — whether to **compress harder** or **escalate the tier**, then assembly runs once more. Drop order when over budget: old window messages → lowest-scored chunks → memory → summary shortening. Never drop system instructions or the user message. Log every drop.

Expose `GET /v1/debug/context/{turn_id}` (admin only) returning the exact `ContextPackage` — the admin UI shows it as the "what did the model see" panel.

> **Amendment 2026-10-05 (ADR-0059).** In phase 02 the agent's `GET /v1/debug/context/{turn_id}` is **owner-only**: it answers only the conversation's owner, and only while `DEBUG_CONTEXT_ENABLED` is true. Admin access is deferred to the phase 03 policy layer, which decides who may read another user's packages through the gateway.

---

## 8. State vs Memory (implement both; keep them apart)

| | State | Memory |
|--|-------|--------|
| What | truth of the running task | facts worth keeping after the task |
| Shape | typed `AgentState`, JSONB via checkpointer | `memory_items(user_id, tenant_id, kind, text, embedding, confidence, source_turn, created_at, expires_at)` |
| Lifetime | conversation | across conversations |
| Written | synchronously, every node | asynchronously after the turn, by `memory/writer.py` |
| Read | loaded whole by the orchestrator | retrieved top-k by similarity |
| Policy | — | `memory/policy.py`: extract candidates with the small model → keep only `kind ∈ {preference, fact_about_user, project_context, decision}` → dedupe by similarity ≥0.92 → **never store PII categories or anything a guard flagged** |

Memory write is a background job (Redis queue + worker inside the agent service). Users can list and delete their memory items from the UI (`/settings/memory`). Admins cannot read users' memory.

---

## 9. Tools and reference capabilities

The subsections below describe the five v1 capabilities. Each one is implemented as a **capability package** (§18); nothing below is a branch in the core graph.

All tools register in `tools/registry.py` with: `name`, `description_ar`, `description_en`, `input_schema` (Pydantic), `risk` (`read|write`), `required_permission`, `timeout_s`, `max_retries`. The `broker.py` is the only path to execution: it resolves secrets from env/Vault, applies policy (§10), enforces timeout/retry, normalizes results to `ToolResult{status, content, sources[], latency_ms}` and emits spans.

### 9.1 SQL tool (UC1) — `capabilities/sql/`

- **Semantic layer** (`config/semantic-layer/`): each exposed table with Arabic + English descriptions, column descriptions, allowed joins, sensitive columns (excluded at the view level, not by prompt), plus `metrics.yaml` (named metrics with canonical SQL) and `examples.yaml` (question → SQL pairs). Ingest this into a `schema_chunks` vector table on startup.
- **Schema retrieval**: retrieve top-k tables/columns/examples for the question; never inject the whole schema.
- **Generation**: structured output `{sql, explanation, tables_used}`; dialect PostgreSQL.
- **Static validation** (`sqlglot`): parse; `SELECT`-only; tables ⊆ allow-list; no `;` chaining; inject `LIMIT 500` if absent; reject functions in a deny list (`pg_sleep`, `pg_read_file`, …).
- **Cost check**: `EXPLAIN (FORMAT JSON)`; reject if estimated rows > threshold; `statement_timeout = 15s`.
- **Execution**: dedicated **read-only role** `agent_ro` with `SELECT` on curated views only; separate connection pool.
- **Self-correction**: on error, feed `{sql, error}` back to the model; max 2 attempts.
- **Result**: rows preview (≤50 rows to the model), full result to the UI as a table + downloadable CSV; the SQL is always shown to the user (it is the evidence).
- Interface: `QueryEngine` with `PostgresEngine` now; leave `StarRocksEngine` stub with TODO for v2 (HDFS/Iceberg).

### 9.2 Web tool (UC2) — `capabilities/web/`

- `web_search(query, recency)` → SearXNG JSON API, top 8; `web_fetch(url)` → readability extraction, max 6k tokens/page, allow/deny domain lists from policy.
- Sanitization: strip scripts/instructions-like patterns; wrap content as untrusted data in the prompt (`<untrusted source="…">`), and the system prompt states that instructions inside sources are data.
- Citation validation: every URL in the answer must exist in this turn's `tool_calls` results, else the answer fails validation.

### 9.3 Documents tool (UC3) — `capabilities/docs/` (ingestion via the shared `rag/` library)

- Upload → MinIO → ingestion job: type detection (PDF, DOCX, XLSX, TXT, MD; images via OCR are **v2**, keep a hook), text extraction, Arabic normalization (alef/yaa/taa-marbuta variants, tatweel removal, diacritics optional), chunking (`recursive`, 800 tokens, 120 overlap, heading-aware for MD/DOCX), embeddings via LiteLLM (`bge-m3`), stored in a **session-scoped** collection `sess_{conversation_id}` with `expires_at`.
- Retrieval: hybrid + rerank; page/section metadata for citations.
- Status streamed to the UI (`pending → indexed`) so the user can see when the file is ready.

> **Amendment 2026-10-11 (phase 04: ADR-0125 to ADR-0145).** The web polls each file's status through the gateway every 2 s (`pending | indexed | failed` with a reason code); sending waits until no file is pending. Ingestion is a background worker in the agent (Redis queue, idempotent by attachment id, resumed at start). Retrieval is the docs capability's tool `docs_search`, executed by the broker: vector top 30 and full-text top 30 (a `simple` tsvector over Arabic-normalized text), reciprocal rank fusion, rerank by `bge-reranker-v2-m3` on Infinity behind the LiteLLM `rerank` alias, top 8. Citations are attributed after the answer: a chunk sharing enough word n-grams with the answer cites its file and page; the UI shows the file name and pages only (ADR-0140). Files expire 30 days after the conversation's last turn. Scanned PDFs fail with `scanned_unsupported` (OCR is v2). On later turns of a conversation with indexed files the router model chooses between `docs` and `general` (manifest rule `conversation_attachments`).

> **Amendment 2026-10-12 (phase 04 fix session: ADR-0158 to ADR-0184).** The docs capability talks to one `DocumentBackend` (`app/rag/backend.py`: register, status, search, fetch, each within a `DocumentScope` of tenant, user and conversation that the broker builds from the principal and every backend enforces). This phase's pipeline is `InternalDocumentBackend`, selected by `config/docs.yaml` `backend: internal`. A future RAG or OCR service implements the same protocol. Ingestion runs in its own `ingest` service (the agent's image, `mem_limit 2g`). Each file is parsed and chunked in a child process with CPU, memory and time limits, after archive, XML, PDF-stream, text and chunk-count checks. Chunks are stored as extracted: no policy runs at ingestion, and every guard policy runs at runtime, per principal. Chunk text never enters `AgentState`; the answer step fetches it through the backend. The product deletes no file and nothing expires; `docs/PROGRESS.md` gives the operator's deletion steps. Retrieval is vector top 30 and full-text top 30 with reciprocal rank fusion, then the top 8 in fusion order. The cross-encoder is off by default (`retrieval.rerank.enabled: false`, ADR-0184): it was the slowest step and every rag threshold held without it. Switched on, it scores the best 16 fused candidates on their first 1070 characters. Citations are attributed after the answer by a generic step before validate (`config/citations.yaml`, ADR-0183). The question's words decide whether a chunk may be cited, and rarity-weighted bigrams of its best sentence decide which.

### 9.4 Knowledge-base plugins (UC4) — `capabilities/kb/`

- Admin creates a KB: `{slug, name_ar, name_en, icon, description, embedding_model, visibility: role-based}`; each KB has its own collection `kb_{slug}` and a Keycloak role `kb:{slug}:read` (created via Admin API when the KB is created).
- Admin uploads documents to a KB (same ingestion pipeline, org-scoped, no expiry, versioned; re-ingest replaces by `doc_id`).
- UI: KB icons appear as toggleable plugins next to the composer **only for KBs the user has the role for**. The gateway re-checks on every turn; the agent receives `selected_kbs` already filtered — and re-filters again against `permissions` (defense in depth).
- Retrieval merges results across selected KBs, keeps per-chunk `kb` attribution, and the answer cites `[KB name › document › section]`.

### 9.5 General (UC5)

No tools. Small context. Still passes input/output guards and validation. Router model is the main cost here; cache aggressively.

---

## 10. Guardrails — generic, dynamic, admin-managed

### 10.1 Model

A **policy** = `{id, name, enabled, version, scope, checkpoints[], checker, config, action, severity, message_ar, message_en}`. Policies are stored in PostgreSQL (`guard_policies`), seeded from `config/policies/*.yaml`, cached in Redis, hot-reloaded on change (pub/sub). Every policy evaluation is recorded in `state.guard_events` and the trace.

**Scope:** `global | tenant:{id} | role:{role} | kb:{slug} | tool:{name}` — narrower scopes override wider ones. A scope says *what a policy applies to*; `tool:{name}` is not a permission name. Permissions are `cap:{slug}` and `kb:{slug}:read` (§11, ADR-0006).

**Checkpoints (7):**

| # | Checkpoint | Sees | Typical policies |
|---|-----------|------|------------------|
| 1 | `INPUT` | user message + attachments metadata | prompt-injection patterns, banned topics, PII in prompt (warn), size limits |
| 2 | `TOOL_LIST` | tool registry for this principal | which tools/KBs are visible to the model at all |
| 3 | `PRE_TOOL` | tool name + args | arg validation, domain allow/deny, SQL table allow-list, `require_approval` |
| 4 | `POST_TOOL` | tool result | untrusted-content sanitization, PII detection in fetched/queried data, size truncation |
| 5 | `OUTPUT` | draft answer + citations | banned topics, tone, refusal templates, citation policy |
| 6 | `DATA` | any text leaving toward the user or memory | PII **redaction** (phone, national ID, IBAN, email…), secret patterns |
| 7 | `MODEL_TIER` | the tier resolver's request (role, step, capability floor, escalations) | ceilings by role, tenant or quota (§19.3); a capped request is served at the ceiling and logged |

**Actions:** `allow | block | redact | rewrite | warn | require_approval | log_only`. `block` returns the policy's `message_ar/en` to the user; `redact` replaces spans with `[REDACTED:{type}]`; `rewrite` asks the model to regenerate with the violation explained (max once).

> **Amendment 2026-10-06 (ADR-0078, ADR-0092, ADR-0093, ADR-0083).** Phase 03 supports the scopes `global`, `tenant:{id}`, `kb:{slug}` and `tool:{name}`; `role:{role}` keeps its precedence slot but is refused, because the agent never sees roles. "Narrower overrides wider" applies within a policy `group` (default: the policy's id); across groups the most restrictive action wins. MODEL_TIER policies are ceilings and use the deterministic `tier_ceiling` checker. A policy also has `on_error` (`closed` for regex, presidio and keyword_list; `open` for llm_judge) and, at DATA, may name `config.targets` (`answer`, `memory_candidate`, `summary`). The Arabic-name heuristic ships as a seventh seed policy, `pii-arabic-names-log`, with action `log_only`.

> **Amendment 2026-10-06 (ADR-0105, ADR-0107).** `role:{name}` scopes are supported: such a policy applies to a user holding the Keycloak realm role `policy:{name}`, the only roles the gateway forwards (`principal.policy_roles`). Precedence stays tool > kb > role > tenant > global; two policy roles conflicting in one group resolve to the stricter action (block > rewrite > redact > require_approval > warn > log_only > allow). A narrower policy that weakens a wider one of its group is an exception: it is flagged when saved, with the policies it overrides, and audited.

### 10.2 Checkers (pluggable — `guardrails/checkers/`)

Each checker implements `check(text_or_payload, config) -> CheckResult{matched, spans[], score, explanation}`:

- `regex` — user-supplied patterns (with named groups for redaction).
- `presidio` — PII entities; ship custom Arabic recognizers for **Saudi mobile numbers** (`05xxxxxxxx`, `+9665…`), **national ID / iqama** (10 digits starting with 1/2), **IBAN (SA…)**, Arabic names heuristic. Configurable entity list and score threshold.
- `keyword_list` — allow/deny lists with Arabic normalization and stemming-light matching.
- `llm_judge` — the primary **topic** filter. Prompt (Arabic + English) with the policy's `topic_description` and few-shot examples; small model; returns `{violates: bool, confidence, category}`; cached by text hash. Admin defines topics in natural language, e.g. *"political opinions about parties or elections"*, *"sectarian or tribal comparisons"*. This is how "don't talk about X social/political topic" becomes a policy without code.
- `classifier` — optional hook for a local guard model (Llama Guard / Granite Guardian) when available; not required for v1.
- `opa` — evaluates a Rego policy for tool authorization (`PRE_TOOL`); v1 ships with a tiny OPA sidecar and one example policy; the checker may be disabled in compose if OPA is not wanted.
- `sql_static` — the §9.1 validator exposed as a checker so its rules are policy-configurable (allow-listed tables, max limit).

### 10.3 Untrusted content rule

Everything that comes back from a tool is data. The `POST_TOOL` checkpoint always runs `sanitize_untrusted` (strip HTML/JS, collapse whitespace, remove strings that look like instructions to the assistant, cap size) **before** any other checker and before the content reaches Context Assembly.

### 10.4 Admin UI for policies

- List / create / edit / enable / version policies; test a policy against sample text and see the `CheckResult`.
- Per-policy metrics from Langfuse/DB: matches per day, false-positive feedback (users can flag "this block was wrong").
- Import/export YAML.

Seed policies in `config/policies/`:
`pii-redact-output.yaml`, `pii-block-in-memory.yaml`, `prompt-injection-input.yaml`, `topics-politics-llm-judge.yaml` (disabled by default, example), `web-domain-denylist.yaml`, `sql-tables-allowlist.yaml`, `tool-visibility-by-role.yaml`.

---

## 11. Identity, authorization, admin

- Keycloak realm `agent` (export in `infra/keycloak/realm-export.json`): clients `web` (public, PKCE) and `gateway` (confidential, service account with `realm-management` roles to manage users/roles).
- Realm roles: `user` (composite; includes `cap:general`), `admin`, one `cap:{slug}` per capability (e.g. `cap:sql`, `cap:web`, `cap:docs`, `cap:kb`), and one `kb:{slug}:read` per KB (created dynamically). Groups map to role bundles (e.g. group *MoE analysts* → `user`, `cap:sql`, `cap:kb`, `kb:moe:read`).
- Token claims → `permissions[]` in the principal. The gateway is the only place that interprets Keycloak roles; the agent only sees `permissions[]`. `tenant_id` comes from the Keycloak user attribute `tenant_id` (mapped into a token claim) and defaults to `default` when absent.
- Admin console (in the web app, `admin` role): users & groups (delegated to Keycloak Admin API through the gateway), KB management + role assignment, policies, evaluation dashboards (links to Langfuse), context debug panel, audit log search.
- Seed users: `admin/admin`, `analyst/analyst` (sql + moe KB), `viewer/viewer` (general + docs only).

> **Amendment 2026-10-11 (phase 04).** The seed users `analyst` and `evaluator` also hold `cap:docs`: smoke uploads a file as the analyst to check the viewer cannot reach it, and the rag and routing suites run as the evaluator.

> **Amendment 2026-10-06 (ADR-0107).** Realm roles prefixed `policy:` (e.g. `policy:pii-exempt`, held by the seed user `analyst`) are policy-scope roles: they grant nothing, select the guard policies scoped `role:{name}`, and are the only roles the gateway forwards to the agent, as `principal.policy_roles`. The admin policy form lists them through the gateway's Keycloak Admin API client.

---

## 12. Observability & evaluation

- OTel SDK in agent and gateway → OTel Collector → Langfuse (traces) and stdout/Prometheus (metrics). Trace per turn: `turn` root span; child spans `context.assemble` (with block token counts), `route`, each node, each `model.call` (model, tokens, cost, latency), each `tool.exec`, each `guard.check` (policy id, action). `conversation_id`, `user_id` (hashed), `tenant_id` as trace attributes.
- Online evaluation (async after turn, sampled `10%` + all reflected turns): LLM-judge scores `faithfulness`, `answer_relevance`, `citation_validity` (deterministic), `guard_false_positive` (when user flags). Scores pushed to Langfuse.
- Offline evaluation (`make eval`): `evals/run_evals.py` runs the golden sets against the live stack, pushes a Langfuse dataset run, and prints a pass/fail table:
  - `sql.jsonl`: question → expected SQL semantics (compare result sets, not SQL text).
  - `rag.jsonl`: question → expected sources/answer; Ragas `faithfulness`, `context_precision`, `answer_correctness`.
  - `routing.jsonl`: message → expected intent.
  - `guardrails.jsonl`: text → expected action per policy (Arabic-heavy cases, including PII variants).
- Fail CI if any suite drops below its threshold in `evals/thresholds.yaml`.

---

## 13. Frontend (Next.js)

- Auth via Keycloak (PKCE, `next-auth` or `oidc-client-ts`); all API calls go to the gateway.
- Pages: `/chat/[id]`, `/chat/new`, `/settings/memory`, `/admin/*`.
- Chat composer: attachments (drag-drop, progress, `indexed` badge), **plugin toggles** rendered entirely from `GET /v1/capabilities` (KBs and tools alike: icon, `name_ar`, tooltip, `ui.mode` = `toggle | auto | button`), persisted per conversation, Arabic RTL default with `<bdi>` isolation for technical terms. The UI has no capability-specific code.
- Message rendering: streaming tokens; event chips inline (`tool_start/end` with latency, `guard` with policy name and action, `citation` list); SQL results as a table with "show SQL" and CSV export; citations open source preview (doc page / URL / KB section).
- "What did the model see" panel (admin only) reading `/v1/debug/context`.
- Feedback: thumbs up/down + "this refusal was wrong" → stored as Langfuse score + audit.

> **Amendment 2026-10-15 (ADR-0192 to ADR-0194, owner decision).** The chat page lists the caller's conversations, newest first, beside the chat, and `/chat/[id]` opens with the conversation's earlier messages. The gateway serves `GET /v1/conversations` and `GET /v1/conversations/{id}/messages`, owner only, audited (`conversation.list`, `conversation.read`); the agent reads the history from the checkpointed thread and the list from `agent.conversation_owners`. The history shows the post-guard text, the files sent with each question and each answer's citations when they were recorded; a retracted exchange is left out. Read-only: no rename or delete (ADR-0166). API version 2.1.0.

---

## 14. Docker Compose (v1 services)

| Service | Image / build | Notes |
|---------|---------------|-------|
| `postgres` | `pgvector/pgvector:pg16` | DBs: `app` (state, memory, KBs, policies, audit), `business` (UC1 seed data, `agent_ro` role), `keycloak`, `langfuse` |
| `redis` | `redis:7` | |
| `keycloak` | `quay.io/keycloak/keycloak:26` | `--import-realm` from `infra/keycloak` |
| `litellm` | `ghcr.io/berriai/litellm` | `infra/litellm/config.yaml` mapping aliases → `OLLAMA_BASE_URL`/`VLLM_BASE_URL` (host models, `host.docker.internal`) |
| `searxng` | `searxng/searxng` | JSON enabled |
| `minio` | `minio/minio` | buckets: `uploads`, `kb`, `langfuse` |
| `presidio-analyzer` | `mcr.microsoft.com/presidio-analyzer` | + custom recognizers mounted |
| `opa` | `openpolicyagent/opa` | optional profile `--profile opa` |
| `langfuse-web`, `langfuse-worker`, `clickhouse` | official | Langfuse v3 stack (uses `postgres`, `redis`, `minio`) |
| `otel-collector` | `otel/opentelemetry-collector-contrib` | |
| `agent` | `services/agent` | internal port 8000 |
| `gateway` | `services/gateway` | port 8080 |
| `web` | `services/web` | port 3000 |

- `.env.example` documents every variable; no secrets in compose. Model endpoints default to Ollama on the host.
- `make up` must bring a working stack with seeded users, one KB (`moe`, with 3 sample Arabic PDFs), UC1 sample schema (`sales`, `customers` with **fake** PII to prove redaction), and pass `scripts/smoke.sh` (login → 5 turns covering the 5 use cases → guard redaction → HITL interrupt/resume → eval run).
- Provide `docker-compose.prod.md` notes for Kubernetes migration (Helm later, not v1).

> **Amendment 2026-10-11 (ADR-0134, ADR-0141).** `rerank` runs `michaelf34/infinity:0.0.75` (amd64 and arm64) on CPU with `bge-reranker-v2-m3` from the volume `rerank-models`, which `make models` fills; nothing is downloaded at start. `litellm` is published on `127.0.0.1` only, behind its master key, so `make eval` can score Ragas metrics through the judge alias. MinIO credentials reach the agent and the gateway from `.env`. `make smoke-airgap` runs smoke with every container but `litellm` on a network without egress.

> **Amendment 2026-10-06 (ADR-0097, ADR-0087).** `presidio-analyzer` is pinned to `2.2.362` with one worker and loads only `infra/presidio/recognizers.yaml`; it takes about 740 MiB. The agent now uses Redis (policy cache, change notifications, judge verdicts) and waits for `redis` and `presidio-analyzer` to be healthy.

> **Amendment 2026-10-05 (ADR-0065, ADR-0066).** `litellm` reads one `infra/litellm/config.yaml` whose aliases take their model, endpoint and key from `.env` (`FAST_MODEL`, `STANDARD_MODEL`, `HEAVY_MODEL`, `EMBED_MODEL`, `MODEL_API_BASE`, `MODEL_API_KEY`, `EMBED_API_BASE`). Two profiles use it: **offline** (the `.env.example` defaults, Ollama on the host, no key; `make smoke` passes on a fresh clone) and **OpenRouter** (development only, §21.4). Switching is a `.env` edit and `make up`; no compose overlay.

---

## 15. Delivery plan (build in this order; each phase ends green)

> **Amendment 2026-10-12, revised 2026-10-14 (owner decisions, phase order).** MCP integration is inserted as phase 5, right after UC3 documents, followed by two new phases, Skills and Planner; the later phases shift. The build order is now: 1 Skeleton & platform, 2 State, Context Assembly, Memory, 3 Guardrails engine, 4 UC3 documents, **5 MCP integration**, **6 Skills**, **7 Planner**, 8 UC4 KB plugins, 9 UC2 web, 10 UC1 Text-to-SQL, 11 Reflection, model routing, HITL and artifact outputs, 12 Evaluation & hardening. The scope of phases 5, 6 and 7 is in `docs/BACKLOG.md` ("MCP integration", "Skills", "Planner"); every done-check is in `CLAUDE.md`. The list below keeps its original numbering.

1. **Skeleton & platform** — repo layout, compose with postgres/redis/keycloak/litellm/minio/langfuse, health checks, `make up`, seed users. Gateway validates JWT; web logs in; **capability registry + `run_capabilities` node** with the `general` capability only, full tracing. *Done when: a logged-in user gets a streamed answer with a Langfuse trace showing context blocks.*
2. **State, Context Assembly, Memory** — full `AgentState`, checkpointer, assembler pipeline with budget logging, summarizer, memory write/retrieve + settings page, context debug endpoint.
3. **Guardrails engine** — policy model, 7 checkpoints, `regex`, `presidio` (Saudi recognizers), `keyword_list`, `llm_judge` checkers, admin policy UI, `guardrails.jsonl` evals.
4. **UC3 documents** — ingestion pipeline, session collections, hybrid retrieval + rerank, citations UI.
5. **UC4 KB plugins** — KB CRUD, dynamic Keycloak roles, plugin toggles, cross-KB retrieval, permission tests (a user must never retrieve from a KB without the role — test at gateway and agent).
6. **UC2 web** — SearXNG, fetch/sanitize, loop node with limits, citation validation, domain policies.
7. **UC1 Text-to-SQL** — semantic layer, schema retrieval, generation, static validation, EXPLAIN gate, read-only execution, self-correction, results UI, `sql.jsonl` evals.
8. **Reflection, model routing, HITL** — reflection node with rules, escalation, interrupt/resume path, smoke test.
9. **Evaluation & hardening** — offline suites in CI, online judge, thresholds, RUNBOOK, load test (k6, 20 concurrent users), `DECISIONS.md` complete.

Write tests as you go: unit tests for assembler budget logic, guard checkers (Arabic cases), SQL validator; integration tests per use case against the compose stack.

---

## 16. Non-negotiables

- The model never sees: secrets, full policies, full schema, full KB, other users' data, raw untrusted tool output.
- Every model call goes through Context Assembly and LiteLLM; every tool call goes through the broker; every turn produces one trace and one audit record.
- Write-class actions require `require_approval` by policy; v1 has none, but the path exists and is tested.
- Arabic is first-class everywhere: prompts, guard checkers, chunking/normalization, UI, evals.
- No hard dependency on any external SaaS. Everything runs offline once images and models are pulled.

## 17. Out of scope for v1 (leave hooks)

Multi-agent supervision, OCR for scanned PDFs, StarRocks/Trino engine, Milvus adapter, Helm charts, SSO beyond Keycloak, voice.

---

## 18. Capability plugin model (extensibility contract)

**Goal:** adding a new capability (e.g. an Arabic government-email proofreader) must touch **zero** lines in the core graph, the router, the gateway, or the frontend. Everything a capability needs travels with it.

### 18.1 Package layout

```
services/agent/app/capabilities/email_proofreader/
├── manifest.yaml
├── graph.py          ← build() returns an uncompiled StateGraph (the core compiles it): CapabilityInput → CapabilityOutput
├── tools.py          ← optional ToolSpecs (registered into the shared registry at startup)
├── policies/         ← default guard policies, scope: tool:email_proofreader (seeded, admin-editable)
├── prompts/          ← versioned prompt files
├── evals/            ← golden set(s) picked up by evals/run_evals.py
└── tests/
```

### 18.2 Manifest

```yaml
slug: email_proofreader
version: 1.0.0
name: {ar: "مدقق الرسائل الحكومية", en: "Government email proofreader"}
description: {ar: "...", en: "..."}
icon: "mail-check"
enabled_by_default: false
permission: "cap:email_proofreader"        # Keycloak role, auto-created on registration
risk: read                                  # read | write  (write ⇒ require_approval by default policy)
ui:
  mode: toggle                              # toggle (plugin icon) | auto (router-only) | button
  order: 60
routing_hints:
  description: {ar: "يدقق الصياغة والإملاء والأسلوب الرسمي لرسالة أو خطاب", en: "Proofreads spelling, grammar and formal tone of an email or letter"}
  keywords: {ar: ["دقق","صحح","صياغة","إملاء","خطاب"], en: ["proofread","fix grammar","rewrite email"]}
  rules:                                    # deterministic pre-router rules
    - when: "toggled"                       # user toggled the icon → always selected
    - when: "keyword"
model:
  min_tier: fast                              # this capability runs fully on the fast tier
  steps: {}                                   # optional per-step override, e.g. {generate: standard, fix_error: fast}
  on_overflow: compress                       # compress | escalate  (what to do when context does not fit)
context:
  needs: [system, state_digest, window, memory]   # which Context Assembly blocks to request
  max_input_tokens: 6000
limits:
  max_iterations: 2
  timeout_s: 30
state_namespace: "cap.email_proofreader"    # isolated slice of AgentState
```

> **Amendment 2026-10-15 (phase 05, ADR-0202, ADR-0213).** Two manifest sections. `documents.collections` (`session`, `kb`) names the kinds of document collection the capability's tools may read; the broker builds the turn's collections from the principal (the conversation's session collection, and each knowledge base the principal may read and the turn selected) and keeps only those kinds; omitted, no document. `declarative: {extract: {role, prompt, schema, on_missing: ask}, steps: [{id, tool, args}], findings: {prompt}}` makes a capability of a manifest, prompts and evals with no `graph.py`: a core runner fills the extract schema with one structured-output call, runs the steps in order through the broker with arguments mapped from `$.extract.<field>`, `$.steps.<id>.result.<path>` or literals, and renders the findings with the capability's prompt. MCP tools are declared in `tools` as `{server}.{tool}` (ADR-0205). `RuntimeContext.documents` holds the document backends by name; config/<slug>.yaml `backend` names the one a capability's tools use.

> **Amendment 2026-10-18 (phase 05 fix session, ADR-0230, ADR-0231, ADR-0238, ADR-0239).** `RuntimeContext` carries no document backend: a tool of a capability declaring `documents.collections` receives a scope-bound facade (search, fetch, status) from the broker, which resolves the backend from `config/<slug>.yaml`. A declarative step may declare `result_list`, `empty_when` and `ambiguous_when`; the core appends every step result inside untrusted delimiters (`context.tool_result`). An MCP document backend reaches its server only through `broker.transport`, whose operations are `class: transport` in config/mcp.yaml and never declared by a capability.

### 18.3 Runtime contract

```python
class CapabilityInput(BaseModel):
    message: str
    context: ContextPackage          # already assembled per manifest.context.needs
    principal: Principal             # user_id, tenant_id, permissions
    cap_state: dict                  # state[manifest.state_namespace]
    attachments: list[Attachment]
    locale: str

class CapabilityOutput(BaseModel):
    findings: str                    # what the answer node synthesizes from
    sources: list[Source] = []       # for citation validation
    artifacts: list[Artifact] = []   # tables, files, diffs
    events: list[Event] = []         # streamed to UI (tool_start/end, progress)
    cap_state: dict                  # persisted back into its namespace
    cost: Usage

class Capability(Protocol):
    manifest: Manifest
    def build(self) -> StateGraph         # uncompiled subgraph consuming CapabilityInput, producing CapabilityOutput; the core compiles it
    def tools(self) -> list[ToolSpec]: ...
```

### 18.4 What the core does with a manifest (automatically)

| Concern | Behaviour |
|---------|-----------|
| Discovery | `capabilities/registry.py` imports every package with a valid manifest at startup; invalid manifests fail fast with a clear error |
| Enable/disable | `admin` can toggle capabilities at runtime (DB flag, Redis pub/sub); disabled capabilities vanish from routing and UI |
| AuthZ | On first registration the gateway creates the Keycloak role named in `permission`; the gateway's only rule is *"user must hold `manifest.permission`"* |
| Routing | `routing_hints` are compiled into the rule set and into the router-model prompt |
| UI | `GET /v1/capabilities` returns manifests filtered by the principal; the composer renders them |
| Context | Assembly builds a `ContextPackage` with only the blocks in `context.needs`, within `max_input_tokens` |
| Model tier | `model.min_tier` and `model.steps` feed the tier resolver (§19); the capability calls `models.call(role=..., step=...)` and never a model name |
| Guardrails | Policies with scope `tool:{slug}` are seeded from `policies/`; all seven checkpoints wrap the capability without it knowing |
| Tools | `tools()` are registered into the shared registry; execution always goes through `broker.py` |
| Observability | One span `capability.{slug}` per invocation with usage and events |
| Evaluation | `evals/run_evals.py` globs `capabilities/*/evals/*.jsonl` and runs them under a suite named by slug |
| State | Each capability reads/writes only `state[state_namespace]`; cross-capability state access is forbidden by a lint rule and a runtime guard |

### 18.5 Non-negotiables for capabilities

- A capability **never** names a model; it requests a role/step and receives whatever tier the resolver chose.
- A capability **never** calls the model directly, reads secrets, executes a tool outside the broker, or touches another capability's state.
- A capability **never** assembles its own context from raw history; it declares needs and receives a `ContextPackage`.
- The five v1 capabilities (`general`, `docs`, `kb`, `web`, `sql`) are built with this exact contract from phase 1 — no special-casing for "built-in" ones.
- `email_proofreader` ships in v1 as a **disabled reference implementation** (Arabic spelling/grammar/formal-tone check producing a diff artifact) to prove the contract; enabling it must require nothing beyond the admin toggle and role assignment.

---

## 19. Model tiers and routing (speed and cost)

**Goal:** every model call runs on the cheapest tier that can do the job, escalation is explicit and logged, and the largest saving — helper calls that happen on every turn — is locked in by configuration, not by cleverness.

### 19.1 Tiers and roles (`config/models.yaml`, served through LiteLLM aliases)

```yaml
tiers:
  fast:     {alias: fast,     window: 32000,  max_output: 2000, cost_weight: 1}
  standard: {alias: standard, window: 32000,  max_output: 4000, cost_weight: 5}
  heavy:    {alias: heavy,    window: 131072, max_output: 8000, cost_weight: 20}

roles:                      # who uses what by default — helper roles are pinned to fast
  router:      fast
  summarizer:  fast
  judge:       fast         # online eval + llm_judge guard checker
  extractor:   fast         # memory candidate extraction, context compression
  reflection:  standard
  reasoning:   standard     # provisional; may be escalated (19.3)

escalation:
  complexity_high: heavy
  risk_high: standard        # never below standard for risky turns
  context_overflow_default: compress   # compress | escalate — capability manifest may override
  on_validation_failure: +1  # one tier up, once
  max_escalations_per_turn: 2

caps:                        # ceilings; also expressible as MODEL_TIER guard policies
  default: heavy
  role:viewer: standard
```

`infra/litellm/config.yaml` maps `fast/standard/heavy/embed/rerank` aliases to concrete endpoints with fallbacks. Swapping a model touches that file only.

### 19.2 API surface inside the agent

```python
models.call(role="reasoning", step="generate", messages=..., schema=...)   # capability code
models.call(role="summarizer", ...)                                          # core code
```
`models/routing.py` resolves `(role, step, capability, turn_context)` → tier, records `{tier, reason}` on the span, and forwards to LiteLLM.

### 19.3 Tier resolution order (per model call)

1. **Floor:** `capability.manifest.model.min_tier` (or the per-step override).
2. **Router escalation:** `intent.complexity == high → heavy`; `intent.risk == high → at least standard`.
3. **Context Assembly escalation:** assemble for the provisional tier; on `NeedsMore`, apply `on_overflow` (capability override → config default): `compress` runs a harder pass (more extractive compression, smaller `top_k`, shorter window); `escalate` moves one tier up and re-assembles. At most one re-assembly.
4. **Validation escalation:** if `validate`/`reflect` fails once, retry the producing step one tier up (bounded by `max_escalations_per_turn`).
5. **Cap:** the `MODEL_TIER` guard checkpoint (a 7th checkpoint, evaluated inside the resolver) applies ceilings by role/tenant/quota. A capped request is served at the ceiling and logged as `capped`.

Final tier = max(1–4) bounded by 5. Every decision is written to the trace as `model.tier_decision {requested, floor, escalations[], cap, final}` so cost reports can answer "why did 30 % of calls go to heavy?".

### 19.4 Rules of thumb baked into the seed config

- Helper roles never leave `fast`. This alone removes most of the cost of a turn.
- `general` capability: `min_tier: standard`, `on_overflow: compress`. The `fast` tier exists for instruction-following helper roles, not for world knowledge; a general-knowledge answer depends on what the model knows (ADR-0024).
- `docs`, `kb`, `web`: `min_tier: standard`, `on_overflow: compress`.
- `sql`: `steps: {generate: standard, fix_error: fast, interpret: fast}`.
- `email_proofreader`: `min_tier: fast`.
- Deep-analysis turns (complexity high + large retrieved context): `heavy`, `on_overflow: escalate`.

### 19.5 Evaluation hook

`evals/golden/routing.jsonl` gains an `expected_tier` field; the routing suite fails if the resolver picks a higher tier than expected on more than 10 % of cases (cost regression) or a lower tier where the answer then fails faithfulness (quality regression).

---

## 20. Static and versioned content (configuration over code)

A deliberate share of the system's behaviour lives in files, not code, so it can change without a deploy and can be reviewed like data. Every item below is **versioned in git, validated at startup, and covered by an eval**.

| Folder | Content | Format | Who edits | Hot reload | Eval that guards it |
|--------|---------|--------|-----------|------------|--------------------|
| `config/prompts/` and `capabilities/*/prompts/` | System, router, summarizer, judge, reflection and per-capability prompts. Jinja2 templates with `{{ }}` variables and a YAML front-matter (`id`, `version`, `role`, `locale`, `changelog`). Never inline strings in Python. | `.md.j2` | prompt owners + engineers | yes (file watcher in dev, admin "reload" in prod) | routing, rag, sql, guardrails suites |
| `config/policies/` and `capabilities/*/policies/` | Guardrail policies (seed) | YAML → DB | admins via UI after seed | yes (DB + Redis pub/sub) | guardrails.jsonl |
| `config/semantic-layer/` | Tables, columns, metrics, example question→SQL pairs | YAML | data owners | on restart (re-indexes `schema_chunks`) | sql.jsonl |
| `config/models.yaml` | Tiers, roles, escalation, caps | YAML | platform team | on restart | routing.jsonl (`expected_tier`) |
| `capabilities/*/manifest.yaml` | Capability identity, routing hints, tier, context needs | YAML | capability owners | on restart (registry); enable/disable at runtime | per-capability evals |
| `services/web/src/i18n/{ar,en}.json` | All UI strings | JSON | product/UX | build | — |
| `infra/*` | Keycloak realm, LiteLLM aliases, SearXNG, OTel | JSON/YAML | platform team | container restart | smoke.sh |

Rules:
- A prompt change is a PR that must show the eval delta (the CI job posts before/after scores).
- Prompts are addressed by `id@version`; the trace records which version produced each call, so a regression can be tied to a prompt change.
- No secrets and no environment-specific values in any of these folders; those live in `.env` / Vault.

### 20.1 Configuration keys added in phase 02

| File | Keys | Meaning |
|------|------|---------|
| `config/context.yaml` | `blocks.<block>.max_tokens`, `blocks.memory.top_k`, `blocks.retrieval.top_k` | Token budget of each Context Assembly block (§7) and how many memory items and chunks are taken |
| | `drop_order` | Order in which the budget guard drops over the tier budget: `window`, `retrieval`, `memory`, then `summary` (shortened) |
| | `compress.*` | The harder pass when `NeedsMore` resolves to `compress`: window size, memory and retrieval `top_k`, summary and tool-result budgets |
| | `summary.trigger_tokens` | History size (summary + unsummarized messages) past which older messages are folded into `state.summary` |
| | `nodes.<node>.request_prompt`, `.forms`, `.blocks` | A helper node's request prompt; evidence form `full`, `summary` or `reference` (§7 step 8); the node's own block budgets |
| `config/memory.yaml` | `enabled_by_default`, `kinds`, `min_confidence`, `max_items_per_turn`, `dedupe_similarity`, `min_similarity`, `max_text_chars`, `ttl_days` | Memory policy (§8): default switch, what is kept, deduplication, retrieval floor, expiry |
| `config/validation.yaml` | `scripts.<locale>.letters`, `scripts.<locale>.mixed_token_allowlist` | Writing scripts an answer in that locale may use, and tokens allowed to mix two scripts |
| | `retry.issues`, `retry.attempts`, `retry.temperature` | The answer's single retry after a script failure |
| `.env` | `DEBUG_CONTEXT_ENABLED` | Records context packages and serves `GET /v1/debug/context/{turn_id}` on the agent; `false` by default |

---

### 20.2 Configuration keys added in phase 03

| File | Keys | Meaning |
|------|------|---------|
| `config/guardrails.yaml` | `sanitize.max_chars`, `sanitize.instruction_patterns` | `sanitize_untrusted` at POST_TOOL (§10.3): size cap and the instruction-like strings removed (matched on Arabic-folded text) |
| | `streaming.terminators`, `streaming.flush_chars` | The DATA sentence buffer (ADR-0089): a sentence leaves after a terminator followed by whitespace, a newline, or `flush_chars` characters |
| `config/policies/*.yaml` | one policy per file (§10.1 fields, plus `group`, `on_error`, `config.targets`) | Seeded into `agent.guard_policies` only when the id is absent (ADR-0086) |
| `config/context.yaml` | `nodes.guard_judge` | The llm_judge checker's node: its system and request prompts (ADR-0102) |
| `config/prompts/guardrails/` | `guardrails.judge.system`, `guardrails.judge.request`, `guardrails.rewrite` | The judge's prompts and the one-rewrite policy hint, in ar and en |
| `infra/presidio/recognizers.yaml` | the recognizer registry | Saudi mobile, national ID / iqama, SA IBAN, email, Arabic-name heuristic (ADR-0097) |
| `.env` | `DEBUG_CONTEXT_RETENTION_DAYS`, `LOCK_POOL_SIZE` | Context package expiry (ADR-0081); connections for per-conversation turn locks (ADR-0082) |

### 20.3 Configuration keys added in phase 04

| File | Keys | Meaning |
|------|------|---------|
| `config/docs.yaml` | `retention_days` | Files and their chunks expire this many days after the conversation's last turn (ADR-0125) |
| | `limits.*` | Bytes per file, files per conversation, pages per PDF, allowed types; read by the gateway and the agent (ADR-0126) |
| | `chunking.*`, `extraction.*`, `ingest.*` | 800/120 chunking, the scanned-PDF threshold, worker count, embedding batch, attempts |
| | `retrieval.*` | Vector and text top-k, RRF constant, rerank candidates, top 8, evidence budget, query stopwords (ADR-0133) |
| | `citations.ngram`, `citations.min_shared_ngrams` | Page attribution after the answer (ADR-0140) |
| | `purge.interval_hours` | How often expired files are deleted |
| `config/routing.yaml` | `router.window_messages`, `router.cache_ttl_s` | The router model's context and its 1-hour cache (ADR-0139) |
| `config/models.yaml` | `rerank.alias` | The LiteLLM alias of the reranker (ADR-0134) |
| `config/prompts/router/` | `router.system`, `router.request` | The router model's prompts |
| `capabilities/docs/prompts/` | `docs.evidence` | Retrieved chunks inside untrusted delimiters (ADR-0136) |
| `.env` | `LITELLM_PORT` | Loopback port of LiteLLM for `make eval` (default 4000) |

### 20.4 Configuration keys added in phase 05

| File | Keys | Meaning |
|------|------|---------|
| `config/mcp.yaml` | `servers[]`: `id`, `url_env`, `auth` (`none` \| `bearer_env`), `internal`, `profiles`, `timeout_s`, `max_result_bytes`, `max_concurrency` | The MCP servers the platform may call, reviewed by PR (D4, ADR-0204); Streamable HTTP only, no URL or secret in the file |
| | `servers[].tools[]`: `name`, `remote_name`, `risk`, `label {ar, en}`, `input_schema_sha256`, `injected` | The tools allowed from each server, read class only (D6), their progress label (I13), their pinned input schema (I5) and the fields the broker fills from the principal and the scope (I6) |
| `config/docs.yaml` | `backend` (`internal` \| `mcp`), `mcp_server` | The document backend of uploads and the docs capability, and the server of config/mcp.yaml the `mcp` backend calls (I11) |
| `capabilities/directions/manifest.yaml` | `declarative.*`, `documents.collections`, `routing_hints.rules: keyword_candidate` | The reference routing capability (ADR-0213, ADR-0215, ADR-0216) |
| `capabilities/directions/prompts/` | `directions.extract`, `directions.findings` | The extract call's instructions and the findings for the answer, ar and en |
| `.env` | `MCP_ROUTING_URL`, `MCP_RAG_URL`, `MCP_RAG_TOKEN` | Where the servers of config/mcp.yaml are, and the RAG reference server's bearer token |
| | `MCP_PROFILE`, `MCP_ALLOW_EXTERNAL` | The profile this deployment runs (`airgap` in the airgap overlay) and the external-server gate, off (D5, I16) |
| | `DOCS_BACKEND` (generally `<SLUG>_BACKEND`) | Overrides a capability's `backend` per environment (ADR-0224) |
| | `RAG_REF_DB_PASSWORD`, `MINIO_RAGREF_ACCESS_KEY`, `MINIO_RAGREF_SECRET_KEY` | The RAG reference server's own database role and MinIO user (ADR-0225) |

> **Amendment 2026-10-18 (phase 05 fix session).** Keys added: config/mcp.yaml `servers[].tools[].class` (`tool` | `transport`, ADR-0230); config/docs.yaml `status_timeout_s` (2: one bound on reading a conversation's file statuses, ADR-0233); a capability's `evals/metrics.yaml` (its eval metrics with generic scorers, ADR-0240); `.env` `MCP_REF_TEST_DELAY_S` (the reference routing server's test-only delay, 0). `mcp-routing-ref` is on `mcp-routing`, a network it shares with the agent only, and CONNECT is kept from PUBLIC on the business, keycloak, langfuse and agent_test databases (ADR-0242).

> **Amendment 2026-10-15 (phase 05, ADR-0211).** Compose gains `mcp-routing-ref` and `mcp-rag-ref`, the reference MCP servers (infra/mcp-ref), on `mcp`, an internal network with no route out, never published; `postgres-roles`, a one-shot job creating the RAG server's database role and schema; and `make mcp-hashes`. The agent does not wait for any MCP server: one that is down is reported in `/v1/health`, and the capabilities that need it say so in their answers.

## 21. Getting started on a new machine

> **Amendment 2026-10-05 (ADR-0066).** Models are no longer named in `infra/litellm/config.yaml` but in `.env`: `make models` and `scripts/preflight.sh` read `FAST_MODEL`, `STANDARD_MODEL`, `HEAVY_MODEL`, `EMBED_MODEL`, `MODEL_API_BASE` and `EMBED_API_BASE` from `.env` (no shell variables). The defaults in `.env.example` are the offline profile. §21.4 below is superseded by the amendment at its top.

### 21.1 Prerequisites

| Tool | Version | Why |
|------|---------|-----|
| Docker Desktop (or Docker Engine + Compose v2) | Compose v2; **12 GB of memory or more** for the Docker VM | The whole stack. `scripts/preflight.sh` warns below 12 GB; the stack itself uses about 6.5 GB, and builds need the headroom. |
| Git | any | Clone the repository. |
| `curl`, `jq`, `openssl`, `python3` | any | `scripts/init_env.sh`, `preflight.sh`, `pull_models.sh`, `smoke.sh`. macOS ships all four; on Debian, `apt install curl jq openssl python3`. |
| `uv` | ≥ 0.4 | `make eval` and `make smoke` run on the host (`uv run` fetches their dependencies). Everything else runs in containers. |
| Ollama | ≥ 0.3 | **Optional.** Only if this machine also serves the models. Skip it when you point at a shared server (§21.4). |

No JDK and no Node.js are needed: the gateway and web tests build inside Docker (ADR-0019).

### 21.2 First run

> **Amendment 2026-10-12 (ADR-0179).** `make models` also fetches the reranker's weights, at a pinned revision, into the external volume `rerank-models`, which `make clean` keeps; run it before `make up`, with a remote model endpoint too (it skips what is served elsewhere, and the reranker when `RERANK_API_BASE` points at another server). `make up` never depends on it: without the weights the `rerank` service idles and says what to run, and `scripts/preflight.sh` warns.

```bash
git clone https://github.com/<org>/agent-platform.git
cd agent-platform

scripts/init_env.sh     # writes .env with fresh random secrets; never overwrites an existing one
make models             # pulls the Ollama models named in .env and fetches the reranker's pinned weights (run it before make up)
make up                 # builds, starts and waits for every health check (first run: ~10 minutes)
make smoke              # end-to-end: login, permissions, a streamed Arabic answer, a Langfuse trace, one audit row
```

Then `make test` (unit + integration, offline through a mock LiteLLM) and `make eval` (golden suites against the live stack with real models).

`make up` is safe to re-run; `make down` stops the stack and keeps the data, `make clean` deletes the volumes too.

### 21.3 URLs and seed users

| What | URL | Credentials |
|------|-----|-------------|
| Chat UI | http://localhost:3000 | the seed users below |
| Gateway API | http://localhost:8080 | Bearer token from Keycloak |
| Keycloak admin | http://localhost:8081 | `KC_BOOTSTRAP_ADMIN_USERNAME` / `KC_BOOTSTRAP_ADMIN_PASSWORD` from `.env` |
| Langfuse | http://localhost:3001 | `LANGFUSE_ADMIN_EMAIL` / `LANGFUSE_ADMIN_PASSWORD` from `.env` |

Ports come from `WEB_PORT`, `GATEWAY_PORT`, `KEYCLOAK_PORT` and `LANGFUSE_PORT` in `.env`; change a port there and change the matching `*_PUBLIC_URL` with it, because the Keycloak URL is also the token issuer.

Seed users in realm `agent` (password = username): `admin/admin` (admin console), `analyst/analyst` (`cap:sql`, `cap:kb`, `kb:moe:read`), `viewer/viewer` (general and docs only).

### 21.4 Using a shared model server

> **Amendment 2026-10-05 (ADR-0065, ADR-0066).** Every alias reads its model and endpoint from `.env`; `infra/litellm/config.yaml` is never edited to change a model. **Switch to OpenRouter** (development only, no real data) by replacing these lines of `.env`, then `make up`:
>
> ```
> FAST_MODEL=openrouter/google/gemma-4-26b-a4b-it
> STANDARD_MODEL=openrouter/qwen/qwen3.6-27b
> HEAVY_MODEL=openrouter/deepseek/deepseek-v4-flash
> MODEL_API_BASE=https://openrouter.ai/api/v1
> MODEL_API_KEY=<OpenRouter key>
> ```
>
> Switch back by restoring the `.env.example` values. Embeddings stay on Ollama (`EMBED_MODEL`, `EMBED_API_BASE`). A remote Ollama or vLLM server is the same edit: `MODEL_API_BASE` (and `EMBED_API_BASE`) pointing at it, with `ollama_chat/<model>` or `openai/<model>` as the model values. `MODEL_REASONING_ENABLED` and `MODEL_PROVIDER_ALLOW_FALLBACKS` (both `false`) are read by OpenRouter and ignored by Ollama. `num_ctx` stays in `infra/litellm/config.yaml`, equal to the tier windows, because LiteLLM passes environment values as strings and Ollama requires an integer. The text below describes the layout before this amendment.

Nothing in the repository names a model except `infra/litellm/config.yaml`, and nothing names an endpoint except `.env`. To use models served elsewhere — a GPU box on the network, or a colleague's machine — leave the code alone and change those two files.

**A remote Ollama server.** In `.env`:

```
OLLAMA_BASE_URL=http://models.internal:11434     # read by the litellm container
```

`host.docker.internal` (the default) means "the Docker host itself"; replace it with a hostname or IP the containers can reach. Two host-side scripts read shell variables instead, because they run outside compose and never parse `.env`:

```bash
OLLAMA_HOST=http://models.internal:11434 make models   # pull the models onto that server
OLLAMA_HOST_URL=http://models.internal:11434 make up   # so preflight checks the right server
```

The remote server must accept non-local connections (`OLLAMA_HOST=0.0.0.0:11434` in its own environment).

**An OpenAI-compatible endpoint** (vLLM, LiteLLM, TGI, or a shared gateway). In `.env`:

```
VLLM_BASE_URL=http://models.internal:8001/v1     # must end in /v1
VLLM_API_KEY=<token, or empty when the server needs none>
```

Then point the alias at it in `infra/litellm/config.yaml`, keeping the alias name — the code only ever names `fast`, `standard`, `heavy`, `embed`:

```yaml
  - model_name: standard
    litellm_params:
      model: openai/Qwen2.5-14B-Instruct     # the name that server serves
      api_base: os.environ/VLLM_BASE_URL
      api_key: os.environ/VLLM_API_KEY
```

`num_ctx` is Ollama-only; on an OpenAI-compatible server the context length is the server's own setting, so keep the `window` values in `config/models.yaml` at or below it. Skip `make models` entirely — that target pulls into Ollama. Aliases may be mixed: one tier remote, another local.
