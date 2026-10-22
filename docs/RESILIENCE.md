# RESILIENCE.md — how the platform keeps one party from stalling others

Read this before adding any inbound path (uploads, streams, client requests) or outbound dependency (model provider, databases, Redis, object storage, MCP servers, web fetch). The rule itself is CLAUDE.md rule 12; this file explains it, records what we built, and lists what production must change.

## Why this exists

Every request is handled separately, but requests share limited things: a thread pool, an event loop, a connection or session to a server, a counter, a breaker. Each stall we found came from one of those shared things, never from requests sharing one thread by design:

| Phase | What happened | Mechanism | Fixed by |
|---|---|---|---|
| 04 | Slow uploads held every gateway thread | Resource exhaustion (Slowloris) | Per-user and global upload caps, a body deadline (ADR-0185, ADR-0190) |
| 05 | One MCP server's long response froze every user's chat | Quadratic parsing on the agent's single event loop | Incremental parsing, size cap over the whole response (round 1 fix) |
| 05 | One user's 2 s status bound shut a server off for everyone | Shared state (breaker) changed by a caller's cancellation | Cancellation never decides the breaker (ADR-0244) |
| 05 | One held reply blocked every call to that server | One shared session with a single writer (MCP SDK) | One pooled session per in-flight call (ADR-0246) |
| Timeout budget PR | Two hostile files at once could get a document container OOM-killed | Shared memory: children's limits summed past the container's | Children sized from measurements, a startup check, no swap (ADR-0252) |
| Timeout budget PR | A slow MCP registration answered the user as failed while the file was registered behind it | An inner bound (62 s) past the outer one (60 s) | The upload answers within 20 s; the registration goes on in the background (ADR-0254) |
| Timeout budget PR | Nothing bounded a turn; the gateway's 60 s cut every turn, body included | No end-to-end deadline; a total timeout read as a header one | One deadline per turn, the relay's own bound, every inner bound nested (ADR-0255 to ADR-0259) |
| Timeout budget PR | A healthy server's restart could count breaker failures | A wait for the next generation counted as the server's outcome | Only an attempt's own outcome decides (ADR-0249) |
| PR #12 review | One user's 20 turns held every turn slot; everyone else got `service_busy` | A shared pool with a platform limit only | Turn limits per user, per tenant and platform-wide (ADR-0262) |
| PR #12 review | Two users' slow registrations could fill a RAG server's slots | The upload's slot freed while its registration held a server slot | A registration counts until it ends; background work takes half the slots (ADR-0265) |
| PR #12 review, round 2 | Six users' registrations queued behind each other could open the rag breaker for everyone | A background call's slot wait was taken from its attempt, and that shortened attempt's timeout counted against the server | The wait bounded on its own, the full timeout_s after it; only the server's full timeout_s counts (ADR-0274) |
| PR #12 review | An answer ending near the deadline was cut after its last token | No room kept for the steps after the answer | A post-answer reserve; never `turn_timeout` once the answer is sent (ADR-0263) |

Patterns and names follow the standard literature: Michael Nygard, *Release It!* (timeouts, circuit breaker, bulkhead); Google, *Site Reliability Engineering*, "Handling Overload" and "Addressing Cascading Failures" (load shedding, retry budgets); Amazon Builders' Library, "Timeouts, retries, and backoff with jitter".

## What we built and the industry-standard alternative

| Concern | What we built | Where | Industry-standard alternative | Verdict |
|---|---|---|---|---|
| Upload protection against slow or flooding clients | Custom code in the gateway: body deadline, concurrency caps, path firewall | Gateway (Java, Spring Boot) | A reverse proxy in front of the gateway (nginx, Envoy, or the entity's ingress): header and body timeouts, max body size, connection limits | Move the connection-level part to the proxy in production. Keep per-user rules in the app (trap 1) |
| Per-user upload rate and concurrency limits | In-memory counters in one gateway process | Gateway | Bucket4j backed by Redis, or Envoy's rate-limit service keyed on a JWT claim | Move to Bucket4j + Redis. Required before running more than one gateway |
| Circuit breaker for MCP servers | Custom implementation | Agent (Python) | No dominant, well-maintained async breaker library in Python; Envoy outlier detection works per host, not per tool or with our cancellation rules | Keep custom (ADR-0237, ADR-0244). Use Resilience4j for any breaker in the gateway |
| Timeouts and connection limits | httpx pool limits and timeouts | Agent | httpx itself | Already standard |
| MCP session pool, response size cap, server-request handling | Custom implementation | Agent | None: the official MCP SDK offers neither pooling nor a response size cap | Keep custom until the SDK provides them (ADR-0246) |
| CPU work off the event loop | Incremental parsing; heavy parsing in a separate process | Agent, ingest service | No library solves this; it is a coding discipline (run_in_executor, process pools) | Keep as rule 12d |
| Nested deadlines (one per call, inner never past outer) | `deadline.within` / `bounded` on anyio cancel scopes and `anyio.current_effective_deadline()`; one context variable carries a scope's end into tasks it creates | Agent (app/resilience/deadline.py) | anyio itself (cancel scopes); `asyncio.timeout` (no effective-deadline lookup across nesting); a custom context-variable stack | Keep anyio as the mechanism (ADR-0250); the context variable only carries the end across `asyncio.create_task`, which anyio does not (ADR-0251) |
| Timeout budget check | scripts/check_timeout_budget.py in `make test`, its rules in services/agent/app/resilience/budget.py: every nested pair of the table below whose values are configured, the margins the code must use, a mutation per rule, the expected rule names; the rules of each capability, tool and MCP server generated from what the configuration declares, none named (scripts/tests) | Repository | None for a project's own configuration | Keep (ADR-0261, ADR-0269, ADR-0275) |
| Concurrency limits per user, per tenant, platform-wide (turns, registrations) | In-process counters, refused at once (`ConcurrencyLimiter`) | Agent (app/resilience/limits.py) | `asyncio.Semaphore` per key (waits, one level); aiolimiter (a rate, not what is in flight); Bucket4j or Envoy's rate-limit service over Redis | Keep for one replica (ADR-0262, ADR-0265); move to Redis with the other limits before a second (phase 12) |
| Lowering an MCP server's max_concurrency at runtime (platform settings) | A holdback of the server's own asyncio semaphore permits: free ones taken at once, the rest as calls return theirs; never past the file's value (`_Holdback`) | Agent (app/mcp/client.py) | A resizable semaphore of our own; replacing the semaphore (old holders plus new callers could pass the ICD figure); aiolimiter (a rate) | Keep: the library keeps waiting, fairness and cancellation (ADR-0281) |
| The gateway's copy of the limits it enforces | A scheduled read of the agent every 5 s, each bounded 4 s, the last values kept on failure (`PlatformLimits`) | Gateway | Spring Cloud Config or a Redis read per request | Keep for one gateway; read per request never (ADR-0283) |
| Answer size cap under the OpenAI SDK, any status | An httpx2 transport counting bytes as they are read (`CappedTransport`) | Agent (app/models/transport.py) | None: neither httpx nor the SDK caps a response; LiteLLM's proxy relays whatever the provider sends | Keep (ADR-0266) |

## Traps when scaling out (more than one gateway or agent replica)

1. **Per-IP limits punish a whole entity.** A reverse proxy sees addresses, not users, and a ministry's employees often leave through one address (NAT). The proxy keeps connection-level protection only (timeouts, body size, a generous connection cap); per-user and per-tenant limits stay in the gateway, which knows the user from the JWT, stored in Redis.
2. **Per-process caps multiply with replicas.** max_concurrency in config/mcp.yaml is per agent process: five replicas send five times as many concurrent calls to a server. Write the cap as a total per server and give each replica its share, or agree the total with the server's owner in the ICD.
3. **Redis becomes critical.** Every limit check reads Redis. Decision for phase 12, recommended: fail open for limits, with a local fallback cap in each process and an immediate alert; concurrency counters carry a TTL so a crashed replica cannot hold slots forever.
4. **Breakers are per process.** Each replica learns a server is down on its own, so a dead server gets a few more attempts. Accepted and standard; do not share breaker state through Redis.

Scaling out never fixes event-loop blocking: every replica blocks the same way. Rule 12d holds at any size.

MCP sessions carry Mcp-Session-Id: a server behind its own load balancer must route a session to the same instance (session affinity). This is the server owner's duty and an ICD item.

## Values in force: files and overrides (platform settings, ADR-0276)

Since the platform settings PR (`feat/platform-settings`, 2026-10-22), the values in the tables below are the **defaults**. An administrator may override some of them from `/admin/settings`, with no deploy.

- **What can be overridden:** the turn limits and deadline, the reserves, the evidence fetch, the model, embedding and rerank ceilings, the upload caps and `register_response_s`, and each MCP server's `max_concurrency`, lowered only. Everything else here changes only with a redeploy.
- **Where to read the values in force:**
  - the page's groups and its timeout tree;
  - the admin API `GET /v1/admin/settings` (each value's default, its value in force and whether it is overridden);
  - the history `GET /v1/admin/settings/versions`, and `agent.platform_settings` in the `app` database (the newest version is in force);
  - the gateway's copy, `GET /v1/internal/settings/limits` on the agent, which it reads every 5 s.
- **Checking:** every save runs the rules `make test` runs (app/resilience/budget.py, on the same values, ADR-0277), so an override cannot break a nested pair.
- **Applying:** a change applies to requests that start after it (ADR-0279). A stored version that no longer passes after a file changed is ignored and the files' values are in force (ADR-0280).
- **Live usage:** the same page shows, read-only and refreshed every 5 s, the turns in flight against their limits, each MCP server's slots, background slots and breaker, background registrations, and `task_abandoned` since start, all from the agent's in-memory counters (ADR-0282).

## Timeout budget (current values)

Values as of the timeout budget PR (`fix/timeout-budget`, 2026-10-21, ADR-0248 to ADR-0261) and its fix session on the PR #12 review (ADR-0262 to ADR-0270). "Library default" means the repository sets nothing and the value is the library's own. Read a row's "Enclosed by" as the bound around it on the same request; "Inner ≥ outer" flags a row whose bound is not shorter than the one enclosing it. An inner bound written "min(X, remaining)" is nested by construction: it is opened with `deadline.within(X)` (services/agent/app/resilience/deadline.py, ADR-0250), so it never runs past what remains of the one enclosing it, whatever X is. `scripts/check_timeout_budget.py` checks, in `make test`, every nested pair below whose two values are configured (ADR-0261). Shortened paths continue the full path above them: gateway Java files are under services/gateway/src/main/java/sa/ncai/agent/gateway/, agent files under services/agent/app/, web files under services/web/src/.

**The I3 finding (ADR-0256).** The table used to say `gateway.agent.response-timeout` bounded the agent's response headers only. A test established otherwise before anything changed: Spring's `JdkClientHttpRequestFactory` applies its read timeout to the whole exchange from the request, and at the timeout it closes the response body wherever the reading is. A turn stream was therefore cut at 60 s total and the relay appended `agent_stream_interrupted`; that is what lost phase 05's eval cases at "the 60 s gateway timeout". A turn stream now has a client of its own bounded by the relay's 135 s; every other agent call keeps 60 s, which covers its body too (StreamBoundTest).

**Chat turn** (web → gateway → agent → LiteLLM → provider)

| Layer | Setting | Value | Source file | Enclosed by | Encloses | Inner ≥ outer |
|---|---|---|---|---|---|---|
| web | Chat request | No timer; the user's stop only | services/web/src/components/Chat.tsx:197 | — | Gateway relay 135 s | Unbounded at the web (phase 12 backlog, T1); the gateway ends the stream at 135 s |
| gateway | Tomcat max-connections / accept-count | 8192 / 100 | services/gateway/src/main/resources/application.yml:33-34 | — | Every request | |
| gateway | `gateway.agent.connect-timeout` | 5 s | application.yml:63; proxy/AgentClient.java:56-59 | — | — | |
| gateway | Turn stream: the agent's response headers | `gateway_headers_s` 15 s (the JDK request timeout); past it 502 `agent_unavailable` (ADR-0264) | config/turn.yaml:19; proxy/TurnStreamRequestFactory.java; proxy/TurnLimits.java | Turn stream 135 s | Agent admission 10 s | |
| gateway | Turn stream: one total bound, request to last byte | `deadline_s` + `gateway_margin_s` = 135 s; past it the stream ends `turn_timeout` and the agent connection is closed (the body closed at the bound) | config/turn.yaml:5,22; proxy/TurnLimits.java; proxy/TurnStreamRequestFactory.java; proxy/SseRelay.java:126; chat/ChatController.java | — | Agent turn 120 s | |
| gateway | `gateway.agent.response-timeout`, every other agent call | 60 s, request to the body's end (Spring closes the body at the bound) | application.yml:67; proxy/AgentClient.java:60-63 | — | Agent register (20 s), status, memory, admin, history calls | |
| gateway | SSE relay | 8192 B buffer; ends at the turn stream's bound | proxy/SseRelay.java:57,126 | Turn stream 135 s | Agent turn | |
| gateway ↔ agent | Keep-alive idle: uvicorn server / JDK HttpClient | 75 s / 30 s (set from `gateway.agent.keep-alive`; JDK 21.0.12's own default, checked, is also 30 s) | services/agent/Dockerfile:43; application.yml:71; HttpClientKeepAlive.java | — | — | Client idle 30 s < server idle 75 s (ADR-0260) |
| gateway | Read of the platform limits from the agent (turn deadline, upload caps) | Every 5 s (`gateway.settings.refresh`), one bound of 4 s from request to body; failure keeps the last values; a change reaches the gateway within 10 s (ADR-0283) | settings/PlatformLimits.java; application.yml | Refresh 5 s | Agent limits endpoint (memory only) | |
| gateway | Hikari pool / connectionTimeout | 10 / 30 s (library default) | application.yml:16 | — | — | |
| agent | Turns in flight: per user / per tenant / platform | 2 / none of its own (the platform's) or `per_tenant_max` with overrides / `LOCK_POOL_SIZE` 20; refused at once: 429 `too_many_turns`, the platform full 503 `service_busy` (ADR-0262) | config/turn.yaml:27-30; services/agent/app/api/runner.py:225; resilience/limits.py | — | Admission | No wait: load shedding |
| agent | Admission: ownership and the lock | `admission_s` 10 s inside the turn's deadline, which opens on the request's arrival; past it 503 `service_busy` (ADR-0264) | config/turn.yaml:7; services/agent/app/api/runner.py:237; app/api/routes.py:112 | Gateway header bound 15 s | Lock `getconn`, ownership reads | |
| agent | Turn lock pool `LOCK_POOL_SIZE` | 20 turns at once | services/agent/app/settings.py:44; docker-compose.yml:47; .env.example:117 | — | — | |
| agent | Lock `getconn` | 5 s, then 503 `service_busy` | services/agent/app/api/locks.py:80,103 | Admission 10 s | — | |
| agent | Wait for a conversation's turn lock | 0 s, then 409 `turn_in_flight` | services/agent/app/api/runner.py; locks.py:97 | Admission 10 s | — | |
| agent | Lease release (unlock, connection back) | 5 s (`RELEASE_TIMEOUT_S`); past it the connection is closed, which ends the session and the lock, and discarded (ADR-0267) | services/agent/app/api/locks.py:35 | — | — | |
| agent | Retract lock wait | 10 s (+ 5 s getconn) | runner.py:76,123 | Gateway response-timeout 60 s | Lock getconn | |
| agent | Whole-turn deadline | 120 s from the request's arrival; at it `turn_timeout`, the graph and every task it owns cancelled, the lock released; once the answer's last token is sent, at least `post_answer_reserve_s` more and never `turn_timeout` (ADR-0263, ADR-0264) | config/turn.yaml:5; services/agent/app/api/runner.py:350,425; resilience/turn.py | Gateway turn stream 135 s | Every agent row below | |
| agent | Answer (model calls, validation retry) | min(its ceiling, remaining − `post_answer_reserve_s` 30 s) (ADR-0263) | config/turn.yaml:12; services/agent/app/graph/nodes/answer.py:128 | Turn 120 s | Model calls | |
| agent | Guards on the whole answer (OUTPUT, DATA) | Each check under its checker's own bound, ending by a horizon `persist_s` before the deadline; cut there, its policy's on_error (ADR-0263) | config/turn.yaml:15; app/graph/nodes/guard.py:116,184; app/guardrails/engine.py | Post-answer reserve 30 s | Judge model call 20 s | Two judged checkpoints can exceed the reserve; the later then ends on its on_error |
| agent | Checkpoint write (persistence) | min(`persist_s` 10 s, remaining) per write (ADR-0263) | config/turn.yaml:15; app/graph/checkpointer.py | Post-answer reserve 30 s | DB write | |
| agent | Summary fold, memory write after the answer | Only with remaining ≥ their role's ceiling (20 s), under it; else `summary_skipped` / `memory_skipped` (ADR-0263) | app/graph/nodes/assemble.py; app/graph/nodes/memory.py | Post-answer reserve 30 s | Model calls, embeddings | |
| agent | After the graph: guard matches, the conversation list | 5 s each (`BOOKKEEPING_TIMEOUT_S`); past it logged, the connection discarded (ADR-0267) | services/agent/app/api/runner.py:93 | — | DB writes | |
| agent | Main DB pool | max 10, getconn 30 s (library default) | services/agent/app/db/__init__.py:52,68-69 | Turn 120 s | — | 20 turns share 10 connections |
| agent | Attachment status bound in a turn | 2 s; the read goes on in the background, detached from the turn's deadline (ADR-0245, ADR-0251) | config/docs.yaml:12; services/agent/app/graph/nodes/attachments.py:26; attachments/service.py | Turn 120 s | rag `status` MCP call, background (slot wait ≤ 30 s, then its attempt 30 s, ADR-0274) | The read outlives the bound by design: it belongs to no caller |
| agent | Capabilities per turn | ≤ 3, one after another | services/agent/app/graph/nodes/route.py:25; graph/nodes/capabilities.py | Turn 120 s | Capability bound | |
| agent | Capability bound | min(manifest `limits.timeout_s`, remaining − `answer_reserve_s` 45 s): default / general / docs / directions 30 / 30 / 60 / 60 s, within the 75 s before the answer; its own limit is `capability_failed`, the reserve `turn_timeout` | services/agent/app/capabilities/contract.py:82; capabilities/{general,docs,directions}/manifest.yaml (30, 34, 42); config/turn.yaml:5; graph/nodes/capabilities.py:327 | Turn 120 s | Model calls, broker tools | |
| agent | Tier escalations per turn | 2 (up to 3 answer calls) | config/models.yaml:39 | Turn 120 s | Model calls | |
| agent | Model call deadline, send to last chunk | min(role ceiling, remaining): router, summarizer, judge, extractor 20 s; reasoning, reflection 90 s. Client per-operation timeout remaining + 1 s; LiteLLM per-request `timeout` remaining − 1 s; one retry, only before the first token, on 429, 5xx or a connection error, with ≥ 10 s left (jitter from an injectable generator). The answer refused past 1 MiB when it is an error or not a stream, past 1 MiB between two newlines when it streams, before the SDK reads it (ADR-0266) | config/models.yaml:57-74; services/agent/app/models/gateway.py; app/models/transport.py:23-24 | Capability bound or turn | LiteLLM per-request timeout | |
| agent | directions extractor calls | Up to 2 model calls (repair), then 3 routing calls; each min(its ceiling, remaining) | services/agent/app/capabilities/declarative.py:157-158 | directions 60 s | Model calls; broker routing 22 s | |
| agent | `docs_search` broker bound, retries | min(45 s, remaining), 0 | services/agent/app/capabilities/docs/tools.py:54-55; tools/broker.py:359 | docs 60 s | Embedding 15 s + rerank 20 s, or rag `search` 30 s | |
| agent | Embeddings: query / ingest batch | min(15 s / 60 s, remaining); per-request timeouts as model calls; one retry; the answer refused past 48 KiB per text before it is parsed, an error answer past 1 MiB before the SDK reads it (ADR-0266) | config/models.yaml:65-67; services/agent/app/models/embeddings.py | `docs_search` 45 s, memory, or an ingest job | LiteLLM | |
| agent | Reranker (off in dev) | min(20 s, remaining); httpx remaining + 1 s, LiteLLM remaining − 1 s; the answer refused past 16 KiB + 256 B per document | config/models.yaml:68; services/agent/app/rag/rerank.py:22,64 | `docs_search` 45 s | LiteLLM | |
| agent | Evidence fetch after the capabilities | min(10 s, remaining − 45 s), one bound for the step; past it the sources not fetched stay without text | config/turn.yaml:7; services/agent/app/graph/evidence.py:116 | Turn 120 s | rag `fetch` MCP call | |
| agent | Presidio checker httpx | 5 s | services/agent/app/guardrails/checkers/presidio.py:20-21 | Turn 120 s | — | |
| agent | `llm_judge` checker | The judge role's model call: min(20 s, remaining); checks run in parallel | config/models.yaml:61; services/agent/app/guardrails/engine.py:200 | Turn 120 s | Model call | |
| agent | Answer validation retry | 0 (off) | config/validation.yaml:19 | — | — | |
| LiteLLM | Per-request `timeout` from the agent | The call's remaining − 1 s | services/agent/app/models/gateway.py:57 | Agent model call | Provider call | |
| LiteLLM | `request_timeout` | 85 s, a backstop under the agent's longest ceiling (90 s) | infra/litellm/config.yaml:73 | Agent model call | Provider call | |
| LiteLLM | `num_retries` | 0; the agent retries | infra/litellm/config.yaml:74 | Agent model call | — | |
| LiteLLM | Fallback heavy → standard | 1 hop, inside the agent's call deadline (tested: ends by it) | infra/litellm/config.yaml:62-65 | Agent model call | Another provider call | |
| LiteLLM | rpm / tpm / max_parallel_requests | Not set | infra/litellm/config.yaml | — | — | No limit |

**Upload** (web → gateway → MinIO → agent register → ingest; status reads)

| Layer | Setting | Value | Source file | Enclosed by | Encloses | Inner ≥ outer |
|---|---|---|---|---|---|---|
| web | Status poll interval | 2000 ms, pending files read one after another in one callback | services/web/src/lib/attachments.ts:8; Chat.tsx:110-121 | — | Gateway status read (agent bound 2 s) | Ticks can overlap (phase 12 backlog, T1) |
| gateway | Uploads per user per minute | 20 baseline; 60 with `.env.example` | config/docs.yaml:24; attachments/UploadRateLimiter.java:33; docker-compose.yml:526; .env.example:122 | — | — | |
| gateway | Declared request size | 20 MiB + 2 MiB, 413 before the body is read | attachments/DocsLimits.java:49,53; AttachmentGate.java:132 | — | — | |
| gateway | Multipart max-file-size / max-request-size | 21 MiB / 22 MiB | attachments/AttachmentsConfig.java:40-41 | — | — | |
| gateway | Concurrent uploads per user / gateway-wide | 2 / 50, then 429 `too_many_uploads` | config/docs.yaml:27-28; DocsLimits.java:37-38; AttachmentGate.java:136 | Tomcat 8192 connections | Body read, MinIO put, agent register | |
| gateway | `upload_deadline_seconds`, whole body | 120 s, then 408; checked per read, so up to about 150 s with a blocked read | config/docs.yaml:29; DocsLimits.java:39; AttachmentGate.java:140-141; DeadlineUploadRequest.java:30-32 | Upload slot | Tomcat `connection-upload-timeout` | |
| gateway | `connection-upload-timeout` / `max-swallow-size` | 30 s / 0 | application.yml:39,42 | Upload deadline 120 s | — | |
| gateway | MinIO Java client | Library default (5 min, not verified in the repo) | attachments/MinioObjectStorage.java:22-25 | Upload slot (no deadline after the body) | MinIO put | Unbounded at the gateway (phase 12 backlog, T1) |
| gateway | Agent register call | `response-timeout` 60 s, request to the body's end | attachments/AttachmentController.java:222; proxy/AgentClient.java:60-63 | Upload slot | Agent register 20 s | |
| agent | Registrations in flight: per user / per tenant / in all | 2 (`max_concurrent_uploads`, counted until each ends, in the background too) / none by default (`max_concurrent_uploads_per_tenant`, overrides) / 50; refused at once, 429 `too_many_uploads` (ADR-0265) | config/docs.yaml:27-33; services/agent/app/attachments/service.py:95; limits.py | — | Register | No wait: load shedding |
| agent | Register: the upload's answer | Within `register_response_s` 20 s: `pending` past it, the hand-over going on in the background, one per attachment, detached; a failure ends the file `failed` `register_failed` (ADR-0254); the answer is the record as stored after it (ADR-0265) | config/docs.yaml:17; services/agent/app/attachments/service.py | Gateway 60 s | Backend hand-over | |
| agent | Register, `mcp` backend → rag `register`, in the background | A background slot first (at most `max_concurrency // 2`, ADR-0265); the slot wait ≤ min(`timeout_s` 30 s, remaining − `timeout_s` − 1), then the attempt's full `timeout_s` 30 s (ADR-0274) | app/mcp/client.py:285,514,547; services/agent/app/rag/mcp.py | None: belongs to no caller | rag MCP call | |
| agent | Register, `internal` backend | Redis RPUSH | services/agent/app/rag/internal.py:36-37 | Register 20 s | — | |
| agent | Files per conversation / PDF pages | 10 / 300 | config/docs.yaml:22-23 | — | — | |
| agent | Status bound, list and single | 2 s, the read goes on in the background, detached, one per attachment | config/docs.yaml:12; attachments/service.py | Gateway 60 s | rag `status` (background: half the slots, slot wait ≤ 30 s, attempt 30 s, ADR-0274) | The read outlives the bound by design (ADR-0245) |
| ingest | Workers | 2 | config/docs.yaml:59 | — | Sandbox, embeddings | |
| ingest | Redis BLPOP | 2 s | services/agent/app/rag/ingest/worker.py:19,53; ingest/queue.py:29 | Redis socket_timeout (library default) | — | |
| ingest | `max_attempts` / backoff | 3 / min(2^n, 30) s | config/docs.yaml:61; worker.py:75-78 | — | Pipeline run | |
| ingest | Per-job deadline | None; up to 125 embedding batches (2000 chunks / 16), 60 s each now | config/docs.yaml:41,60; ingest/pipeline.py | — | Embeddings 60 s | Unbounded (phase 12 backlog, T1) |
| ingest | Sandbox CPU / wall / join grace | 60 s / 120 s / 5 s, then kill | config/docs.yaml:71,73; ingest/sandbox.py | Job (none) | Parser | |
| ingest | Sandbox memory per child × workers + reserve vs container | 640 MiB × 2 + 512 MiB = 1792 MiB ≤ `mem_limit` 2g, no swap (`memswap_limit` 2g); the service refuses to start otherwise (ADR-0252) | config/docs.yaml:72,74; ingest/sandbox.py:91,112; ingest/service.py; docker-compose.yml:474-475 | Container 2 GiB | Child | |
| ingest | DB pool | max 4 | services/agent/app/rag/ingest/service.py | — | — | |
| ingest | Extraction and archive caps | 1 000 000 chars; 50 MB; 1000 entries; 100 MB; ratio 200 | config/docs.yaml:48-56 | — | — | |
| mcp-rag-ref | Index workers / DB pool / container memory | 2 / 8 / 2g, no swap, the same sandbox limits and the same startup check | infra/mcp-ref/mcp_ref/rag/server.py:56,173; docker-compose.yml:418-419 | Container 2 GiB | Sandbox 2 × 640 MiB + 512 MiB | |

**MCP call** (broker → app/mcp/client.py → app/mcp/transport.py → server)

| Layer | Setting | Value | Source file | Enclosed by | Encloses | Inner ≥ outer |
|---|---|---|---|---|---|---|
| config | Defaults: `timeout_s` / `max_result_bytes` / `max_concurrency` | 10 s (≤ 120) / 256 KiB (≤ 16 MiB) / 8 | services/agent/app/mcp/config.py:92-94 | — | — | |
| config | routing: `timeout_s` / `max_result_bytes` / `max_concurrency` | 10 s / 256 KiB / 8 | config/mcp.yaml:22-24 | — | — | |
| config | rag: `timeout_s` / `max_result_bytes` / `max_concurrency` | 30 s / 1 MiB / 8 | config/mcp.yaml:47-49 | — | — | |
| broker | MCP tool bound, retries 0 | min(2 × `timeout_s` + 2, remaining): routing 22 s | services/agent/app/mcp/tools.py:33,124; app/tools/broker.py:359 | Capability (directions 60 s) | Slot wait + attempt | |
| broker | `broker.transport` (rag operations) | min(2 × `timeout_s` + 2, remaining): rag 62 s | app/mcp/tools.py:186 | `docs_search` 45 s, evidence 10 s, status 2 s (outlived by design), or none (background) | Slot wait + attempt | |
| client | Slot wait (no slot free) | ≤ min(`timeout_s`, remaining − `timeout_s` − 1); none left: `tool_busy` at once, breaker untouched; a free slot is taken at once | client.py | Broker bound | — | |
| client | Background work (detached: registrations, status reads) | One of `max_concurrency // 2` background slots first (ADR-0265); the slot wait bounded on its own as a caller's is, then the attempt's full `timeout_s`; only a timeout the server held for the full `timeout_s` from the send counts (ADR-0274) | client.py:285,514,547 | None: belongs to no caller | Attempt | |
| client | Attempt deadline | min(`timeout_s`, remaining − 0.5 s), covering the session, the request and the I9 re-send; the caller's deadline ending it is no verdict (ADR-0259) | client.py:78,556 | Broker bound | Session open + send + re-send | |
| client | Session `initialize` | `timeout_s`, in the session's own task | client.py:232 | Attempt deadline (for a call waiting on it) | — | |
| client | Transport wall-clock deadline, send to last byte | `timeout_s` | app/mcp/transport.py; client.py:201 | Attempt deadline | httpx | |
| client | SDK `read_timeout_seconds` | `timeout_s` + 1 s | client.py:79,227 | Attempt deadline | — | Outer by intent: the transport's deadline always decides |
| client | httpx timeout, per operation | `timeout_s` + 1 | client.py:208 | Transport deadline | — | Outer by intent |
| client | Protocol message cap (initialize, tools/list) | 1 MiB | transport.py:51 | — | — | |
| client | tools/list at connect / session DELETE on close | `timeout_s` / `timeout_s` | client.py | — | — | |
| client | Session pool | ≤ `max_concurrency` per server | client.py | Semaphore | — | |
| client | Breaker | 5 consecutive failures, open 30 s, one trial; a wait for the next generation never counts (ADR-0249) | client.py:64-65 | — | — | |
| client | Reconnect backoff | min(30, 0.5 × 2^(n−1)) s × jitter 0.8–1.2 | client.py | — | — | |
| client | Re-send after an unknown session (I9) | Once, its wait for the next generation inside the attempt deadline | client.py:556 | Attempt deadline | — | |
| mcp-rag-ref | Server-side search: embedding / rerank | 15 s / 20 s (config/models.yaml, the same gateways) | infra/mcp-ref/mcp_ref/rag/server.py:175-177 | Client rag 30 s | — | The client cuts at 30 s; the server's work may go on, within these |

**Shared**

| Layer | Setting | Value | Source file | Enclosed by | Encloses | Inner ≥ outer |
|---|---|---|---|---|---|---|
| agent | Redis client | No timeout set; socket_timeout 5 s in installed redis-py 8.1.0, none in older versions the `>=5.2` pin allows | services/agent/app/main.py | — | — | Possibly unbounded (phase 12 backlog, T1) |
| agent | MinIO (Python) | 300 s, pool 10, 5 retries (library default) | services/agent/app/attachments/objects.py:20 | — | — | |
| agent | DB open at startup | 60 s | services/agent/app/db/__init__.py:79 | — | — | |
| collector | OpenTelemetry batch timeout / memory limit | 2 s / 256 MiB, off the turn path | infra/otel/collector.yaml:21,23 | — | Langfuse export | |
| redis | `maxmemory-policy` | noeviction | docker-compose.yml:76 | — | — | |

## Known residuals

- A background status read lives up to 2 × timeout_s (60 s for rag): a slot wait of at most timeout_s, then its own timeout_s, in at most half the server's slots (ADR-0274; ADR-0265 had one deadline of timeout_s for both, which counted a server answering in time as failed when the wait was long). It stalls no one (verification of PR #10, Q3); it runs without the turn's deadline (ADR-0251).
- A registration answered `pending` goes on in the background within a slot wait of at most the server's timeout_s and then its own timeout_s (30 s each for rag, ADR-0274), counted toward its user's uploads in flight until it ends (ADR-0265); if the agent stops meanwhile, the file stays `pending` (ADR-0254).
- LiteLLM does not close the provider's request when the agent cancels before the first byte, or an embedding: it holds it until the request's own timeout, which the agent sets 1 s before the call's deadline. Bound: never past the call's deadline; LiteLLM's `request_timeout` (85 s) for any caller. Measured: a call cancelled at 1 s with a 6 s deadline, closed 5.03 s after send; once streaming, it closes at once (0.05 s) (ADR-0257).
- LiteLLM turns a large provider error into its own on its own event loop, about 5 s per MiB before the agent sees a byte (measured: 0.04 s, 1.34 s, 5.32 s, 10.57 s for 0, 256 KiB, 1 MiB, 2 MiB); another user's call through it waited 10.3 s meanwhile. The agent refuses such an answer 1 ms after it arrives, unparsed, loop lag 3.7 ms (ADR-0266). Phase 12 backlog, with the flood below.
- A task that ignores its cancellation (a buggy capability) is abandoned after the drain's 1 s grace, logged `task_abandoned` with its node and counted (`agent.tasks_abandoned`), and runs on unbounded; only process isolation of capabilities can stop it (ADR-0054, ADR-0268). Phase 12.
- LangGraph's executor waits for its node tasks without a bound when a graph run ends; the turn's scope cancels it again, so the turn ends, but a node task that ignores its cancellation is left behind as above. Phase 12 backlog.
- A hung rag server is never judged by an evidence fetch: the step's bound (10 s) is shorter than the server's `timeout_s` (30 s), so every fetch ends by its caller's deadline, which is no verdict (ADR-0244, ADR-0259); docs_search calls still count. Phase 12 backlog.
- LiteLLM relays a flooding stream on its own event loop: another user's call through LiteLLM waited 4.02 s while 20 000 one-character chunks were relayed, and 0.56 s while a 2 MiB embedding was; the agent's own loop lag stayed 32 ms and 43 ms (ADR-0257). Phase 12 backlog.
- A hung or trickling answer ends `turn_timeout` up to 1 s before the turn's deadline, when LiteLLM gives up as told (ADR-0258).
- Model provider slowness and 429s in development now reach users as `turn_timeout` (120 s) or `model_unavailable` after the agent's one retry, no longer at a 60 s gateway cut.
