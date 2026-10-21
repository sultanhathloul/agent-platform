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

## Traps when scaling out (more than one gateway or agent replica)

1. **Per-IP limits punish a whole entity.** A reverse proxy sees addresses, not users, and a ministry's employees often leave through one address (NAT). The proxy keeps connection-level protection only (timeouts, body size, a generous connection cap); per-user and per-tenant limits stay in the gateway, which knows the user from the JWT, stored in Redis.
2. **Per-process caps multiply with replicas.** max_concurrency in config/mcp.yaml is per agent process: five replicas send five times as many concurrent calls to a server. Write the cap as a total per server and give each replica its share, or agree the total with the server's owner in the ICD.
3. **Redis becomes critical.** Every limit check reads Redis. Decision for phase 12, recommended: fail open for limits, with a local fallback cap in each process and an immediate alert; concurrency counters carry a TTL so a crashed replica cannot hold slots forever.
4. **Breakers are per process.** Each replica learns a server is down on its own, so a dead server gets a few more attempts. Accepted and standard; do not share breaker state through Redis.

Scaling out never fixes event-loop blocking: every replica blocks the same way. Rule 12d holds at any size.

MCP sessions carry Mcp-Session-Id: a server behind its own load balancer must route a session to the same instance (session affinity). This is the server owner's duty and an ICD item.

## Timeout budget (current values)

Values as of the merge of PR #10 (main at aee98d3). "Library default" means the repository sets nothing and the value is the library's own. Read a row's "Enclosed by" as the bound around it on the same request; "Inner ≥ outer" flags a row whose bound is not shorter than the one enclosing it. Shortened paths continue the full path above them: gateway Java files are under services/gateway/src/main/java/sa/ncai/agent/gateway/, agent files under services/agent/app/, web files under services/web/src/.

**Chat turn** (web → gateway → agent → LiteLLM → provider)

| Layer | Setting | Value | Source file | Enclosed by | Encloses | Inner ≥ outer |
|---|---|---|---|---|---|---|
| web | Chat request | No timer; the user's stop only | services/web/src/components/Chat.tsx:197 | — | Gateway agent call | Unbounded |
| gateway | Tomcat max-connections / accept-count | 8192 / 100 | services/gateway/src/main/resources/application.yml:33-34 | — | Every request | |
| gateway | `gateway.agent.connect-timeout` | 5 s | application.yml:63; proxy/AgentClient.java:49 | — | — | |
| gateway | `gateway.agent.response-timeout` | 60 s, response headers only; the SSE body is unbounded | application.yml:64; proxy/AgentClient.java:52-53 | — | Agent turn | Unbounded body |
| gateway | SSE relay | 8192 B buffer, no idle timeout | proxy/SseRelay.java:57 | Headers-only 60 s | Agent turn | Unbounded |
| gateway ↔ agent | Keep-alive idle: uvicorn server / JDK HttpClient | 5 s (library default) / 30 s (JDK default, not verified in the repo) | services/agent/Dockerfile:40; proxy/AgentClient.java:47 | — | — | Client idle 30 s ≥ server idle 5 s: the gateway may reuse a connection the agent closed (not tested) |
| gateway | Hikari pool / connectionTimeout | 10 / 30 s (library default) | application.yml:16 | — | — | |
| agent | Turn lock pool `LOCK_POOL_SIZE` | 20 turns at once | services/agent/app/settings.py:44; docker-compose.yml:47; .env.example:117 | — | — | |
| agent | Lock `getconn` | 5 s, then 503 `service_busy` | services/agent/app/api/locks.py:80,103 | Gateway 60 s | — | |
| agent | Wait for a conversation's turn lock | 0 s, then 409 `turn_in_flight` | services/agent/app/api/runner.py:161; locks.py:97 | Gateway 60 s | — | |
| agent | Retract lock wait | 10 s (+ 5 s getconn) | runner.py:67,114 | Gateway 60 s | Lock getconn | |
| agent | Whole-turn deadline | None | runner.py:190-240 | Gateway (headers only) | Every agent row below | Unbounded |
| agent | Main DB pool | max 10, getconn 30 s (library default) | services/agent/app/db/__init__.py:52,68-69 | Turn (none) | — | 20 turns share 10 connections |
| agent | Attachment status bound in a turn | 2 s; the read goes on in the background (ADR-0245) | config/docs.yaml:12; services/agent/app/graph/nodes/attachments.py:26 | Turn (none) | rag `status` MCP call (≤ 62 s) | Inner ≥ outer by design (ADR-0233, ADR-0245) |
| agent | Capabilities per turn | ≤ 3, one after another | services/agent/app/graph/nodes/route.py:25; graph/nodes/capabilities.py:355 | Turn (none) | Capability `timeout_s` | |
| agent | Capability `limits.timeout_s`: default / general / docs / directions | 30 s / 30 s / 60 s / 60 s | services/agent/app/capabilities/contract.py:82; capabilities/{general,docs,directions}/manifest.yaml (30, 34, 42) | Turn (none) | Model calls, broker tools | |
| agent | Tier escalations per turn | 2 (up to 3 answer calls) | config/models.yaml:39 | Turn (none) | Model calls | |
| agent | `MODEL_TIMEOUT_S`, OpenAI client, `max_retries` 0 | 300 s per operation (between stream chunks, not total) | settings.py:30; services/agent/app/models/gateway.py:45-46 | Capability 30 / 60 s | LiteLLM `request_timeout` | 300 s ≥ capability 30 / 60 s |
| agent | directions extractor calls | Up to 2 model calls (repair), then 3 routing calls at ≤ 15 s each | services/agent/app/capabilities/declarative.py:157-158 | directions 60 s | Model 300 s; broker 15 s | 2 × 300 s + 45 s ≥ 60 s |
| agent | `docs_search` broker timeout, retries | 45 s, 0 | services/agent/app/capabilities/docs/tools.py:54-55 | docs 60 s | Embedding, rerank or rag `search` | |
| agent | Embeddings client, `max_retries` 0 | 300 s | services/agent/app/models/embeddings.py:18-19 | `docs_search` 45 s | LiteLLM | 300 s ≥ 45 s |
| agent | Reranker httpx (off in dev) | 60 s | services/agent/app/rag/rerank.py:39,53 | `docs_search` 45 s | LiteLLM | 60 s ≥ 45 s |
| agent | Evidence fetch after the capabilities | No bound of its own | services/agent/app/graph/evidence.py:79 | Turn (none) | rag `fetch` MCP call | Unbounded outer |
| agent | Presidio checker httpx | 5 s | services/agent/app/guardrails/checkers/presidio.py:20-21 | Turn (none) | — | |
| agent | `llm_judge` checker | No bound of its own; model client 300 s; checks run in parallel | services/agent/app/guardrails/engine.py:200 | Turn (none) | Model call | |
| agent | Answer validation retry | 0 (off) | config/validation.yaml:19 | — | — | |
| LiteLLM | `request_timeout` | 300 s | infra/litellm/config.yaml:69 | Agent model client 300 s | Provider call | 300 s = 300 s (equal) |
| LiteLLM | `num_retries` | 1 (up to 2 × 300 s) | infra/litellm/config.yaml:70 | Agent model client 300 s | Provider call | 600 s ≥ 300 s |
| LiteLLM | Fallback heavy → standard | 1 hop | infra/litellm/config.yaml:62-65 | Agent model client 300 s | Another provider call | ≥ 300 s |
| LiteLLM | rpm / tpm / max_parallel_requests | Not set | infra/litellm/config.yaml | — | — | No limit |

**Upload** (web → gateway → MinIO → agent register → ingest; status reads)

| Layer | Setting | Value | Source file | Enclosed by | Encloses | Inner ≥ outer |
|---|---|---|---|---|---|---|
| web | Status poll interval | 2000 ms, pending files read one after another in one callback | services/web/src/lib/attachments.ts:8; Chat.tsx:110-121 | — | Gateway status read (agent bound 2 s) | Poll period 2 s ≤ the 2 s read it waits on: ticks can overlap |
| gateway | Uploads per user per minute | 20 baseline; 60 with `.env.example` | config/docs.yaml:19; attachments/UploadRateLimiter.java:33; docker-compose.yml:526; .env.example:122 | — | — | |
| gateway | Declared request size | 20 MiB + 2 MiB, 413 before the body is read | attachments/DocsLimits.java:49,53; AttachmentGate.java:132 | — | — | |
| gateway | Multipart max-file-size / max-request-size | 21 MiB / 22 MiB | attachments/AttachmentsConfig.java:40-41 | — | — | |
| gateway | Concurrent uploads per user / gateway-wide | 2 / 50, then 429 `too_many_uploads` | config/docs.yaml:22-23; DocsLimits.java:37-38; AttachmentGate.java:136 | Tomcat 8192 connections | Body read, MinIO put, agent register | |
| gateway | `upload_deadline_seconds`, whole body | 120 s, then 408; checked per read, so up to about 150 s with a blocked read | config/docs.yaml:24; DocsLimits.java:39; AttachmentGate.java:140-141; DeadlineUploadRequest.java:30-32 | Upload slot | Tomcat `connection-upload-timeout` | |
| gateway | `connection-upload-timeout` / `max-swallow-size` | 30 s / 0 | application.yml:39,42 | Upload deadline 120 s | — | |
| gateway | MinIO Java client | Library default (5 min, not verified in the repo) | attachments/MinioObjectStorage.java:22-25 | Upload slot (no deadline after the body) | MinIO put | Unbounded at the gateway |
| gateway | Agent register call | `response-timeout` 60 s | attachments/AttachmentController.java:222 | Upload slot | Agent register | |
| agent | Register, `mcp` backend → rag `register` | Semaphore ≤ 30 s + attempt ≤ 32 s = ≤ 62 s | services/agent/app/rag/mcp.py:40-55; attachments/service.py:73 | Gateway 60 s | rag MCP call | 62 s ≥ 60 s when the semaphore is full |
| agent | Register, `internal` backend | Redis RPUSH | services/agent/app/rag/internal.py:36-37 | Gateway 60 s | — | |
| agent | Files per conversation / PDF pages | 10 / 300 | config/docs.yaml:17-18 | — | — | |
| agent | Status bound, list and single | 2 s, the read goes on in the background, one per attachment | config/docs.yaml:12; attachments/service.py:21,106,126 | Gateway 60 s | rag `status` (≤ 62 s) | Inner ≥ outer by design (ADR-0245) |
| ingest | Workers | 2 | config/docs.yaml:54 | — | Sandbox, embeddings | |
| ingest | Redis BLPOP | 2 s | services/agent/app/rag/ingest/worker.py:19,53; ingest/queue.py:29 | Redis socket_timeout (library default) | — | |
| ingest | `max_attempts` / backoff | 3 / min(2^n, 30) s | config/docs.yaml:56; worker.py:75-78 | — | Pipeline run | |
| ingest | Per-job deadline | None; up to 125 embedding calls at 300 s each | config/docs.yaml:36,55; ingest/pipeline.py | — | Embeddings 300 s | Unbounded |
| ingest | Sandbox CPU / wall / join grace | 60 s / 120 s / 5 s, then kill | config/docs.yaml:60,62; ingest/sandbox.py:75,99,105 | Job (none) | Parser | |
| ingest | Sandbox memory per child × workers vs container | 1024 MB × 2 vs `mem_limit` 2g | config/docs.yaml:61; sandbox.py:76-77; docker-compose.yml:470 | Container 2 GiB | Child | 2 × 1 GiB ≥ 2 GiB (RLIMIT_AS is virtual memory) |
| ingest | DB pool | max 4 | services/agent/app/rag/ingest/service.py:42 | — | — | |
| ingest | Extraction and archive caps | 1 000 000 chars; 50 MB; 1000 entries; 100 MB; ratio 200 | config/docs.yaml:43-51 | — | — | |
| mcp-rag-ref | Index workers / DB pool / container memory | 2 / 8 / 2g, same sandbox limits | infra/mcp-ref/mcp_ref/rag/server.py:56,173; docker-compose.yml:417 | Container 2 GiB | Sandbox 2 × 1 GiB | 2 × 1 GiB ≥ 2 GiB |

**MCP call** (broker → app/mcp/client.py → app/mcp/transport.py → server)

| Layer | Setting | Value | Source file | Enclosed by | Encloses | Inner ≥ outer |
|---|---|---|---|---|---|---|
| config | Defaults: `timeout_s` / `max_result_bytes` / `max_concurrency` | 10 s (≤ 120) / 256 KiB (≤ 16 MiB) / 8 | services/agent/app/mcp/config.py:92-94 | — | — | |
| config | routing: `timeout_s` / `max_result_bytes` / `max_concurrency` | 10 s / 256 KiB / 8 | config/mcp.yaml:22-24 | — | — | |
| config | rag: `timeout_s` / `max_result_bytes` / `max_concurrency` | 30 s / 1 MiB / 8 | config/mcp.yaml:47-49 | — | — | |
| broker | MCP tool bound `timeout_s` + 5, retries 0 | routing 15 s (rag is transport, no tool) | services/agent/app/mcp/tools.py:31,122-123; app/tools/broker.py:355 | Capability (directions 60 s) | Semaphore wait + attempt (routing ≤ 22 s) | 22 s ≥ 15 s when the semaphore is full: the broker's `tool_timeout` fires before the client's `tool_busy` |
| broker | `broker.transport` (rag operations) | No bound of its own | app/tools/broker.py:156 | `docs_search` 45 s, status 2 s, or none | Client call (≤ 62 s) | |
| client | Semaphore wait | ≤ `timeout_s`, then `tool_busy`, breaker untouched | client.py:258,484-487 | Broker bound | — | |
| client | Attempt backstop | `timeout_s` + 2 (routing 12 s, rag 32 s) | client.py:69,527 | Broker bound | Session open + send | Opening a session (≤ `timeout_s`) then sending (≤ `timeout_s`) is 2 × `timeout_s` ≥ backstop |
| client | Session `initialize` | `timeout_s` | client.py:215 | Attempt backstop | — | |
| client | Transport wall-clock deadline, send to last byte | `timeout_s` | app/mcp/transport.py:216,225,245; client.py:186 | Attempt backstop | httpx | |
| client | SDK `read_timeout_seconds` | `timeout_s` | client.py:210 | Attempt backstop | — | Equal to the transport deadline: which fires first is a race |
| client | httpx timeout, per operation | `timeout_s` + 1 | client.py:193 | Transport deadline | — | Outer by intent |
| client | Protocol message cap (initialize, tools/list) | 1 MiB | transport.py:51 | — | — | |
| client | tools/list at connect / session DELETE on close | `timeout_s` / `timeout_s` | client.py:324,237 | — | — | |
| client | Session pool | ≤ `max_concurrency` per server | client.py:19-23,261 | Semaphore | — | |
| client | Breaker | 5 consecutive failures, open 30 s, one trial | client.py:64-65,103,133 | — | — | |
| client | Reconnect backoff | min(30, 0.5 × 2^(n−1)) s × jitter 0.8–1.2 | client.py:66,314 | — | — | |
| client | Re-send after an unknown session (I9) | Once, with an unbounded `connected.wait()` inside | client.py:532-541 | Attempt backstop | — | Bounded by the backstop only |
| mcp-rag-ref | Server-side search: embedding / rerank | 300 s / 60 s | infra/mcp-ref/mcp_ref/rag/server.py:175-177 | Client rag 30 s | — | The client cuts at 30 s; the server's work may go on |

**Shared**

| Layer | Setting | Value | Source file | Enclosed by | Encloses | Inner ≥ outer |
|---|---|---|---|---|---|---|
| agent | Redis client | No timeout set; socket_timeout 5 s in installed redis-py 8.1.0, none in older versions the `>=5.2` pin allows | services/agent/app/main.py:288-290 | — | — | Possibly unbounded |
| agent | MinIO (Python) | 300 s, pool 10, 5 retries (library default) | services/agent/app/attachments/objects.py:20 | — | — | |
| agent | DB open at startup | 60 s | services/agent/app/db/__init__.py:79 | — | — | |
| collector | OpenTelemetry batch timeout / memory limit | 2 s / 256 MiB, off the turn path | infra/otel/collector.yaml:21,23 | — | Langfuse export | |
| redis | `maxmemory-policy` | noeviction | docker-compose.yml:76 | — | — | |

## Known residuals

- A background status read lives up to about 2 × timeout_s + 2 s (62 s for rag): waiting for a slot, then the read. It stays within the server's max_concurrency and stalls no one (verification of PR #10, Q3).
- Model provider slowness and 429s in development reach users (the 60 s gateway timeout); phase 12 backlog.
