# BACKLOG.md — decided future work not yet in a phase prompt

Purpose: items the owner decided to do later, with enough context that a future chat with Claude, or a build session, can pick them up without earlier transcripts. Read with `docs/HANDOFF.md`. When an item enters a phase prompt, mark it "→ phase NN" here; never delete history.

---

## MCP integration → phase 05 (owner decision, 2026-10-12)

Status: first proposed as phase 10; the owner moved it to run right after phase 04. Phase order since the owner decision of 2026-10-14: Skills 06, Planner 07, kb 08, web 09, sql 10, reflection, tiers, HITL and artifact outputs 11, evaluation and hardening 12 (README §15 amendment note; done-checks in `CLAUDE.md`). Phase 05 scope is trimmed: artifact outputs and long-running tool calls (items 7 and 8 below) move to phase 11, with the interrupt/resume path. No real MCP server is ready yet (owner, 2026-10-12): phase 05 builds the standard against reference MCP servers in the repo; the owner's servers are wired later by configuration and a contract test run. Not started.

### Why
The platform has no MCP support. Every tool today is first-party Python inside a capability, executed by `tools/broker.py`. Two needs require MCP:
1. **Document backend over MCP.** The owner's existing RAG + Arabic OCR service should be usable as the backend of the docs capability, instead of, or beside, the internal pipeline. Phase 04 prepared this with the `DocumentBackend` interface (fix item C6): `register`, `status`, `search`, `fetch`, plus a backend contract test suite.
2. **New capabilities built on MCP tools**, not tied to one use case. Owner's examples:
   - a `routing` capability on the internal GraphHopper MCP server built by the owner's team ("best route to the Boulevard in north Riyadh");
   - a page-builder agent exposed over MCP that produces HTML pages.

### Principles (do not break)
- **Every MCP tool call goes through the broker.** No capability talks to an MCP client directly. The broker keeps scope injection from the verified principal, PRE_TOOL / POST_TOOL checks, audit and the manifest's tool declarations (fix item S1).
- **MCP results are untrusted data**: sanitized at POST_TOOL, sent to the model in the user role inside the untrusted delimiters (fix item S2), checked by runtime policies per principal.
- **Two kinds of MCP server:**
  - *Internal*: runs inside the sovereign environment (the owner's RAG/OCR service, an internal geospatial/places service built on the owner's Riyadh geodata). Allowed in every profile, including air-gapped.
  - *External*: on the internet (e.g. Google Maps). Disabled by default; never available in the air-gapped profile; enabling one is an explicit owner/management decision per server, with a PRE_TOOL policy defining what may leave (no personal data, no internal locations). CLAUDE.md "no external SaaS" stays the default.
- **Allowlist by configuration**: MCP servers and the tools exposed from each are declared in config (e.g. `config/mcp.yaml`), versioned, reviewed by the owner. No discovery of unlisted tools at runtime.
- **Capabilities stay zero-core-change**: a capability declares the MCP tools it uses in its manifest, like any tool.
- **One capability per integration, not one generic "all MCP tools" capability.** Each integration gets its own manifest: routing description for the router, its own Keycloak permission (`cap:{slug}`), tier, policies (`tool:{server}.{tool}`), prompts and evals. A single capability exposing every MCP tool to the model would lose per-integration permissions, routing clarity and evaluation.
- **Secrets** for MCP servers live in `.env` / secret store, never in config files or code.
- **Process isolation**: the hardening phase (phase 12) decides process isolation before any third-party code (ADR-0054). MCP servers run as separate processes/services by nature; the platform-side client still runs behind the broker with timeouts and size limits.

### Scope sketch (to refine into a build prompt)
Phase 05 status (2026-10-15): items 1–6, 9 and 10 → phase 05 (docs/PHASE-05-BUILD-PROMPT.md, ADR-0195 to ADR-0229); items 7 and 8 stay in phase 11. Item 6's capability is named `directions` (ADR-0215).
1. → phase 05. Platform MCP client as a tool provider of the broker: connect to configured servers, map their tools to broker tools (`tool:{server}.{tool}` policy scope), timeouts, result size caps, audit.
2. → phase 05. `config/mcp.yaml` with server list, transport, auth reference, allowed tools, internal/external flag, profiles where enabled.
3. → phase 05. `MCPDocumentBackend` implementing `DocumentBackend`, built and tested against a reference RAG MCP server in the repo (it exposes the internal pipeline over MCP, so `rag.jsonl` runs end to end on both paths); the owner's RAG/OCR MCP server is wired later by configuration; passes the backend contract test suite; `config/docs.yaml backend: mcp` switches it.
   - Backend selection is configuration, never routing: the router picks the docs capability; config picks the backend. Later options, decided on eval numbers: per file type (scanned PDFs to the MCP backend for OCR, text files internal) or per tenant, with results merged in one search.
   - The guard point stays `docs_search`: it is the only tool the docs capability sees; scope injection and PRE_TOOL / POST_TOOL run there. The adapter's calls to the RAG server go through the platform MCP client (allowlist, auth, timeouts, size caps, audit) as transport. The RAG server's own tools are never declared to any capability, so the model cannot call them around `docs_search`.
4. → phase 05 (OCR cases wait for the owner's RAG/OCR server). Evaluation: the same `rag.jsonl` runs against both backends; the switch is decided on the numbers (retrieval hit@8, citation page accuracy, faithfulness, abstention, latency). OCR cases (scanned PDFs) added for the MCP backend.
5. → phase 05. **Declarative MCP capability**: a capability made of a manifest, prompts and evals only, no Python. The manifest names the MCP tools, how the model fills their arguments (structured output against the tool's input schema) and how results become findings. Python stays possible when a flow needs custom logic. Adding a simple integration = one `config/mcp.yaml` entry + one capability folder + one Keycloak role.
6. → phase 05 (slug `directions`). Reference capability: `routing` (geocode origin and destination, then route) on a reference routing MCP server in the repo with a small fixed Riyadh fixture graph, proving zero core change; the owner's GraphHopper MCP server replaces it by configuration when ready.
7. **Artifact outputs** (→ phase 11; needed by a page-builder agent): a capability may return a file (HTML, later others) instead of, or beside, text. Stored in MinIO, shown in a sandboxed preview (iframe sandbox, strict CSP, never rendered inline in the app), downloadable, passed through runtime DATA policies on its text.
8. **Long-running tool calls** (→ phase 11): an MCP call that takes minutes runs as a background job with progress shown to the user and resumable state (reuses the phase 04 ingestion worker pattern and the interrupt/resume path, phase 11).
9. → phase 05. Smoke and airgap: internal MCP servers work with egress blocked; external ones are absent from the airgap profile.
10. → phase 05. **Generalise DocumentScope first** (PR #4 review round 2, finding 1): today evidence is fetched only from the conversation's own files (scope = user + conversation, one backend for every capability). Before kb (phase 08) and the MCP backend: a scope names collections (the session's, and kb collections allowed by the principal's kb:{slug}:read), injected by the broker; evidence is fetched per source's collection and backend; more than one backend is reachable through RuntimeContext. Without this, kb needs a core change.

### Integration contract (ICD) to settle with each MCP server before wiring it
| Item | Question to answer |
|---|---|
| Tools and schemas | Exact tool names, input and output JSON schemas, versions |
| Scope | How the server isolates data per user / conversation / tenant; the platform always passes scope from the verified principal; the server must enforce it (a search must never return another user's document) |
| Mandatory fields (documents) | Chunk text, page, section, file name, score; without page numbers the source line cannot work |
| File transfer (documents) | File bytes over MCP, or an internal reference to the MinIO object; size limits |
| Authentication | Service account, mTLS or token; rotation |
| Errors and limits | Error codes, max payload, timeouts, rate limits, expected latency |
| Data handling | What the server stores, for how long, and where (sovereignty) |
| Versioning | How tool changes are announced without breaking the platform |
| Concurrency limit | The total concurrent calls the server accepts across all platform replicas |
| Session affinity | A server behind a load balancer routes one Mcp-Session-Id to one instance |

### Owner's servers (after phase 05)
- Wire each by URL in `.env` (`url_env`, a bearer token in `bearer_env` when it needs one), add its entry and tools to `config/mcp.yaml` with the hashes `make mcp-hashes` prints, run the DocumentBackend contract suite (a RAG server) and the evals; settle the ICD table above with its team first.

### Deferred from phase 04 that bears on the MCP document backend
- → phase 05 (D7, ADR-0201: indexed with a `pages_without_text` warning the web shows; the pages themselves still wait for OCR). **Partly scanned PDFs**: a partly scanned PDF is indexed from its text pages only; its scanned pages are not read, and nothing tells the user. A PDF fails `scanned_unsupported` only when its text averages under `extraction.min_text_chars_per_page` per page over the whole file. OCR is v2, behind the `Extractor` hook. The OCR cases of scope item 4 should include a partly scanned PDF. (Source: PROGRESS phase 04, fix session round 1, R5; ADR-0128.)

---

## Before the phase 06 build (from PR #10 verification)

Status (2026-10-21): the owner chose the standalone PR (D1, ADR-0248); all seven items → done in fix/timeout-budget (docs/TIMEOUT-BUDGET-PROMPT.md, ADR-0248 to ADR-0261, docs/PROGRESS.md "Timeout budget"), with the PR #12 review's decisions (ADR-0262 to ADR-0270).

- **Generation reset may count breaker failures against a healthy server** (suspected, not reproduced). The verifier of PR #10 suspects, from reading the code only, that when one call finds its MCP session gone while another call waits for a fresh session, the generation-reset path can count breaker failures against a healthy server (`services/agent/app/mcp/client.py`, ADR-0246). Reproduce it first; fix only if reproduced. → done in fix/timeout-budget: reproduced (a call queued for a slot during another call's I9 reset counted four failures against a healthy server in 50 iterations) and fixed (ADR-0249).

Timeout budget findings (`docs/RESILIENCE.md`, timeout budget table), most severe first:
- **High: sandbox children can OOM-kill the document containers.** ingest and mcp-rag-ref each allow two sandbox children of 1 GiB in a 2 GiB container; two hostile files at once can get the whole container OOM-killed, stopping document reading for every user (CLAUDE.md rule 12). The children's combined cap plus the parent's own memory must stay under the container limit. → done in fix/timeout-budget (ADR-0252, ADR-0253).
- **High: MCP upload registration outlasts the gateway.** An MCP upload registration can take up to 62 s against the gateway's 60 s; the user sees a failed upload while the file is registered behind it. The registration's bound must be shorter than the gateway's. → done in fix/timeout-budget (ADR-0254).
- **High: no end-to-end bound on a chat turn.** The gateway's 60 s covers response headers only; the SSE stream and the agent's turn are unbounded. Add a whole-turn deadline that releases the conversation lock and every resource the turn holds. → done in fix/timeout-budget (ADR-0255, ADR-0256; the 60 s turned out to cover the body too).
- **Medium: inner timeouts longer than the ones enclosing them.** Model calls 300 s inside capability limits of 30 or 60 s; LiteLLM's retry up to 2 × 300 s inside the agent's 300 s; embeddings 300 s and rerank 60 s inside docs_search's 45 s; an MCP call up to 22 s inside the broker's 15 s when a server's queue is full. Each inner bound shorter than its outer one, and a cancelled outer call aborts the inner request. → done in fix/timeout-budget (ADR-0250, ADR-0251, ADR-0257, ADR-0259).
- **Low: MCP transport deadline and SDK read timeout race.** Both are `timeout_s`; set the SDK read timeout above the transport deadline so the deadline always decides. → done in fix/timeout-budget (ADR-0259).
- **Low: keep-alive mismatch.** The gateway keeps idle connections to the agent 30 s while the agent closes them after 5 s; the gateway's idle time must be shorter than the agent's, which may also explain some intermittent failures attributed to the model provider. → done in fix/timeout-budget (ADR-0260).
- **Recommendation (decided by the owner on 2026-10-21, D1, ADR-0248):** one standalone infrastructure PR before phase 06 covering the breaker item and these six, verified with `make test` and `make smoke` (CLAUDE.md rule 13: infrastructure only, no eval).

---

## Skills → phase 06 (owner decision, 2026-10-14)

Status: → phase 06 (docs/PHASE-06-BUILD-PROMPT.md, ADR-0289 to ADR-0323; docs/PROGRESS.md "Phase 06: Skills"). Done-check in `CLAUDE.md` (phase 06). Reference skills: official_memo and internal-comms (copied unmodified from github.com/anthropics/skills; official_letter removed on 2026-10-25, ADR-0319); the other candidates below stay for later PRs.

### Tech debt: first item of phase 06, before any new eval suite (owner decision, PR #10 review round 2, 2026-10-19)
- → done in phase 06 (D5, ADR-0293, ADR-0314). **Lost or interrupted cases count as failures in every metric.** Today rag and rag@mcp hit@8, citation page accuracy, faithfulness and answer correctness average only the cases that produced a result (`evals/run_evals.py` `rag_scores`, line 443 at 09a02a4): a case lost to `agent_stream_interrupted` or a provider error drops out of the denominator. Run make-eval-20261018T131835Z lost 7 rag cases and still passed. Every metric must score a lost case as a failure.

### Tech debt: phase 06 (PR #10 fix session, smoke attempt 2)
- → done in phase 06 (D6, ADR-0294, ADR-0314). **Smoke's G7 checks measure platform-side time**, outside model generation spans, not the whole turn, so provider slowness neither fails them nor hides a stall. Smoke attempt 2 of the narrow fix session (2026-10-19) failed a hung-server general turn at 7.45 s against a 6.34 s bound because of provider latency, not the MCP server (`docs/PROGRESS.md`, phase 05, narrow fix session).

### What a skill is
- A skill describes **how** to produce an output or carry out a task: the form, the template, worked examples. It never says **where** information comes from; that stays in capabilities. Example: the docs capability finds the facts in an uploaded file; the "official memo" skill says how the memo is laid out (heading, reference, body, closing, signature block).
- Skills compose with any capability: the same memo skill works over docs, routing, kb, web or sql findings.
- A skill is a folder `skills/<slug>/SKILL.md`: name and description in Arabic and English, when to use it, the capabilities or MCP tools it needs, the Keycloak role that may use it. The router selects a capability and at most one skill; only the selected skill's instructions are loaded, as a reviewed procedural block.
- Owner decisions (2026-10-21, 2026-10-22): a skill names the capabilities it needs, never MCP tools directly. The router selects at most one skill automatically; the chat composer also offers an optional skill picker, with no confirmation prompt. A selected skill is applied when the answer is written (the answer node), never inside run_capabilities: a skill shapes the output and fetches nothing. Skills use the open Agent Skills format, so a public skill loads unmodified.

### Principles
- Written by the development team as files in the repo, through a PR with evals (README §20: prompts are code). No admin editing.
- No scripts until a sandbox exists (hardening, phase 12). A skill is text only.
- Text outputs first; file outputs (for example HTML pages) when artifact outputs arrive in phase 11.
- Adding a skill needs no core change.

### Candidate skills from government work
- Official memo (reference skill in phase 06).
- Official letter between entities (reference skill in phase 06).
- Meeting minutes.
- Monthly report in the department template.
- Reply to a citizen enquiry.

---

## Planner → phase 07 (owner decision, 2026-10-14)

Status: not started. Done-check in `CLAUDE.md` (phase 07).

### Why before kb
The planner fixes the structured-output contract every later capability is built on: a step's inputs may reference an earlier step's outputs, so capabilities declare structured output schemas in their manifests. Building kb, web and sql first would mean retrofitting each of them to that contract.

### Principles
- Simple requests bypass the planner: single-step routing as today, with no added latency.
- A plan is a list of steps (capability, MCP tool or skill). Dependent steps run in order, independent ones in parallel. Every step runs through the broker, with the same scope injection and guards as a single-step turn.
- Step and time budgets. When a step fails, the answer is partial and states what is missing.
- Plan progress is shown to the user.
- No human approval until phase 11, which adds approval as a step that can pause a plan.
- **Privacy**: personal locations or personal data in a plan must never reach an external tool. A PRE_TOOL policy on web and external MCP tools enforces it.
- **Open owner question**: may home addresses be stored in memory? Today the PII policy covers mobile, ID, IBAN and email, not addresses.

### Where the planner sits in the graph (owner decisions, 2026-10-22)
- Routing and planning are one decision at two sizes: a single capability is a one-step plan. The route node decides first whether a request is simple or compound; for a compound request it selects no capabilities itself.
- A new plan node sits right after route and before guard(TOOL_LIST), reached only through a conditional edge for compound requests. A simple request goes from route to guard(TOOL_LIST) directly and never pays the planner's latency. The plan node is separate so it shows as its own step in traces.
- guard(TOOL_LIST) checks every step of the plan; a step the user may not run is removed before execution, and the answer states what is missing.
- run_capabilities becomes the plan executor: independent steps run together in one super-step (LangGraph Send), dependent steps wait for the outputs they reference; every step through the broker with the same scope injection and guards.
- A skill step is applied in the answer node, over the outputs of the earlier steps.
- A plan step is a capability or a skill, never an MCP tool directly: the capability carries the Keycloak role, its policies and its evals, and calling its tool around it would skip all three. This corrects the wording "(capability, MCP tool or skill)" in the Planner section, in CLAUDE.md (phase 07 row) and in docs/PROGRESS.md (phase 07); the phase 07 build changes those lines and README §5.2 (the graph) with an ADR.
- Example: «كم مدة إجازة الزواج؟» goes route → guard(TOOL_LIST). «ابحث في اللائحة عن مكان الاجتماع، واحسب المسار إليه، واكتب مذكرة» goes route → plan → guard(TOOL_LIST) → run_capabilities (docs, then directions with the place from docs) → answer (official memo skill over both outputs).

### Reference compound request (owner's example)
The user uploads a file of companies and their locations, then asks in one message to:
1. search the file;
2. search the web for the sources it lists;
3. find the fastest route from the user's home to company X;
4. build an HTML page with the result.

Phases it needs: 04 docs (search the file), 05 MCP (routing), 06 skills (the page's form), 07 planner (one plan over the steps), 09 web (the listed sources), 11 artifact outputs (the HTML page). Phase 07's own reference case is the part available by then: docs → routing (reference MCP server) → official memo skill.

---

## kb phase (phase 08) — carried from phase 04

- **DocumentScope generalisation** must land first: see MCP item 10 above (it is scheduled in phase 05). Done in phase 05 (ADR-0202): kb is a manifest with `documents.collections: [kb]` and a backend whose `kb_{slug}` collections hold the KB documents. (Source: PROGRESS phase 04, fix session round 2, item 5.)
- **KB documents need their own record for their chunks**: `agent.rag_chunks.attachment_id` is a foreign key to `agent.attachments` with ON DELETE CASCADE (migration 0010), so a chunk cannot exist without an attachment record. (Source: ADR-0166.)
- **`kb:` and `tool:` exceptions at OUTPUT and DATA stay refused**: page attribution says which pages an answer drew on, not which sentence came from which source, so an exception scoped to one KB cannot apply to its part of the answer. Revisit with the kb capability. (Source: ADR-0146.)
- **Re-measure the reranker on large KB documents**: phase 04's measurement used six small documents of at most 40 chunks, where fusion alone put the answer in the top 8 every time. (Source: ADR-0184; also listed under the hardening phase below.)
- **Routing for all capabilities** (owner decision of 2026-10-18, ADR-0247): decide whether every turn goes to the router model or keyword candidacy (ADR-0216) stays, with the router's latency measured and the keyword-free coverage set of `evals/golden/routing.jsonl` (ADR-0241; 0/9 in runs make-eval-20261018T141208Z and make-eval-20261018T150216Z) as the measure. Until then keyword candidacy stays for directions.
- **The eval harness still names docs** (tech debt, PR #10 review round 2, 2026-10-19): in `evals/run_evals.py` (lines at 09a02a4) the document-case branch (line 712) never reads a capability's `metrics.yaml`; `name == "rag"` decides the second backend run (line 659); `DOCS_BACKEND` is set by name (lines 640–646). A kb suite with document cases and its own `metrics.yaml` must run with no harness change (zero core change, ADR-0240).

---

## Hardening phase tech debt (was phase 09, then phase 10 after the MCP shift, now phase 12 after the 2026-10-14 order; notes rather than risks unless stated)

From phase 03:
- OUTPUT `rewrite` prompt must carry the policy topic only, never checker detail or the cache marker.
- Same-tenant timing of cached judge verdicts (ADR-0118); note, not a risk.
- Asynchronous memory write (about 8 s after the answer, measured in phase 02).
- Production realm export without seed users or passwords.
- Role removal takes effect only when the 300 s access token expires.
- Actor display names in the policy change log come from the user-editable Keycloak profile; lock name edits or always show the username beside them.

From the phase 03/04 discussions:
- **Checkpoint history pruning**: about 16 checkpoint rows per turn; keep the latest checkpoint per conversation and any checkpoint awaiting approval (interrupt/resume, phase 11), prune older ones after a set period. Audit history lives in `gateway.audit_events`, not in checkpoints.
- **MinIO licensing and distribution** before production: MinIO under a suitable licence, or the entity's existing S3-compatible storage (the code speaks S3, so it is configuration only).
- **Reranker on GPU in production** (management resource decision). Phase 04 measured (ADR-0184): with rerank off, citation page accuracy 0.91–0.94; with rerank on (16 candidates, 1070 characters) 0.97–1.00, other metrics equal. Rerank is off in development (CPU) and on in production on a GPU (owner decision pending confirmation). Re-measure on large KB documents in phase 08.
- Citation pages on long documents: the two twenty-pages cases miss their page in every run (phase 04 final evals).
- Some rag cases end in agent_stream_interrupted when rerank is slow (phase 04 Q1 runs); check stream timeouts against retrieval latency. (Note, fix/timeout-budget: that end was the gateway's 60 s total timeout, body included, ADR-0256; turns now end `turn_timeout` at 120 s and rerank is bounded at 20 s.)
- Refused upload attempts (400 invalid path, 408, 413, 429) are not audited (ADR-0189 to ADR-0191 era); add rate-limited audit entries or metrics so abuse attempts are visible.
- Generic slow-request (slowloris) exposure on non-upload gateway paths: a slow body to any public path is read without the upload deadline or ceiling (PR #4 final verification, pre-existing). In production, a reverse proxy or ingress with request header and body timeouts in front of the gateway; or gateway-wide read deadlines.
- A model-provider 429 mid-stream ends the turn after the gateway's 60 s agent timeout (phase 04 final eval, routing docs-summary); review provider fallback and the timeout. (Note, fix/timeout-budget: an error mid-stream is now `model_unavailable` at once, a 429 before the first token gets one agent retry, ADR-0257.)

From phase 04 (added from PROGRESS and the ADRs, 2026-10-14):
- **Upload limits live in one gateway's memory**: the per-user rate and concurrency caps and the gateway-wide cap on uploads in flight are counted in the gateway process (one process in v1, ADR-0109). More than one gateway replica needs a shared counter (for example Redis). (Source: ADR-0185, ADR-0190.)
- **Development `.env` files created before ADR-0188** have no `UPLOADS_PER_MINUTE` and run at the strict 20 per minute, so `make eval` waits out 429s; copy `UPLOADS_PER_MINUTE=60` from `.env.example`. A note for the runbook. (Source: PROGRESS phase 04, narrow fix of 2026-10-14; ADR-0191.)
- **Docs latency not re-measured with rerank off**: the E3 measurements (time to indexed p50 2.42 s; docs answer TTFT p50 7.72 s, p95 8.13 s) were taken at the build, with the reranker on (16 candidates, first 400 tokens). The fix sessions measured `rag.retrieve` only (p50 0.13 s with rerank off). Re-run `scripts/measure_docs.py` for the production configuration. (Source: PROGRESS phase 04, E3 and the Q1 table; ADR-0184.)

From phase 05 (owner decision, fix session on PR #10, 2026-10-18; not implemented in phase 05):
- **Scoped LiteLLM keys per internal service**: `mcp-rag-ref` receives `LITELLM_MASTER_KEY`, following the existing precedent (the agent and the ingest service hold it too). Give each internal service a scoped LiteLLM key limited to the aliases it needs (the RAG server: `embed`, and `rerank` when it is on).

From phase 05 (resilience, `docs/RESILIENCE.md`, CLAUDE.md rule 12):
- **Move connection-level upload protection to a reverse proxy** (nginx, Envoy or the entity's ingress: header and body timeouts, max body size, a generous connection cap). Per-user and per-tenant rules stay in the gateway (RESILIENCE trap 1).
- **Move per-user and per-tenant limits to Bucket4j + Redis**, required before running more than one gateway (supersedes the in-memory counters of ADR-0185 and ADR-0190).
- **Settle the four scaling traps of `docs/RESILIENCE.md`**: fail open for limits when Redis is down (recommended), with a local fallback cap per process and an immediate alert; a TTL on concurrency counters so a crashed replica cannot hold slots; MCP `max_concurrency` written as a total per server and shared between replicas (or agreed in the ICD); breakers stay per process.

From the timeout budget PR (fix/timeout-budget, 2026-10-21; T1: listed, not fixed):
- **Rows of the timeout budget still unbounded** (docs/RESILIENCE.md): the ingest service's per-job deadline (up to 125 embedding batches of 60 s each); the agent's Redis client (no timeout set; 5 s only in the installed redis-py); the gateway's MinIO Java client (library default, not verified); the web's chat request (no timer; the gateway ends the stream at 135 s); overlapping status polls in the web (a 2 s period over a 2 s read).
- **LiteLLM relays a flooding stream on its own event loop**: another user's call through LiteLLM waited 4.02 s while one provider sent 20 000 one-character chunks, and 0.56 s while one sent a 2 MiB embedding; the agent's own loop stayed under 50 ms (ADR-0257). Measure under the k6 load test and decide on LiteLLM workers or replicas.
- **A healthy MCP server's restart fails calls at once**: a call that arrives or gets its slot while the next generation is being opened fails `tool_unavailable` instead of waiting the few milliseconds for it (scope A runs: up to 139 of 400 calls in the worst scenario; no breaker failure, ADR-0249). Waiting for the next generation within the attempt deadline would fix it; it changes how a dead server fails (later rather than at once).
- **A cut agent read maps to 500 in the memory proxy**: a non-stream agent call whose body outlasts the gateway's 60 s is answered 500 `internal_error`, not 502 `agent_unavailable` (StreamBoundTest, ADR-0256).

From the fix session on the PR #12 review (fix/timeout-budget, 2026-10-21):
- **In-process limit counters to Redis**: the turn limits (config/turn.yaml `per_user_max`, `per_tenant_max`) and the registrations in flight (config/docs.yaml `max_concurrent_uploads`, `max_concurrent_uploads_per_tenant`) count in the agent's process, one replica in v1 (ADR-0262, ADR-0265). Move them to Redis with the other limits (the gateway's upload rate and concurrency, RESILIENCE.md trap 2) before a second agent replica runs.
- **Production turn capacity is a resource decision**: LOCK_POOL_SIZE (20 in development) is the platform's turns at once; size it per deployment from the k6 load test's measurements (lock and database connections, model throughput), with `per_tenant_max` set for a multi-tenant deployment (ADR-0262).
- **LiteLLM turns a large provider error into its own on its own loop**: about 5 s per MiB before the agent sees a byte (10.57 s for 2 MiB); another user's call through LiteLLM waited 10.3 s meanwhile. The agent refuses the answer unparsed (ADR-0266). Measure with the flood item above; decide on LiteLLM workers, replicas or a proxy cap in front of the provider.
- **Tech debt: a hung rag server never trips the breaker through evidence fetches**: the evidence step's bound (10 s) is always shorter than the server's `timeout_s` (30 s), so every fetch ends by its caller's deadline, which is no verdict (ADR-0244, ADR-0259); only docs_search's calls count.
- **Tech debt: LangGraph's unbounded `asyncio.wait` on exit**: when a graph run ends its executor waits for its node tasks without a bound; the turn's scope cancels it again, but a node task that ignores its cancellation is left running (ADR-0268, with process isolation of capabilities above).
- **Known residual: a healthy MCP server's restart can count a stray breaker failure, rarely** (owner decision of 2026-10-22, ADR-0273): it never opens the breaker on its own, since the breaker counts consecutive failures only and the next success resets the count; restarts are planned maintenance with notice. Measured so far (scratch harness, 2026-10-22, at bbd342d): 3 counts in 2000 seeded iterations of four calls (0.15 %), 7 in 300 with the restart landing while a call is held at the server (2.3 %), none with it pinned to the slot, session open, initialize, initialized or send step (300 each, pooled and cold); `make test` of 2026-10-21T16:18Z counted one (`four-calls`, iteration 5). The one path found (a 404 on the I9 re-send) is fixed (ADR-0272): 0 counts in the same runs after it; other paths are not ruled out, and the investigation stopped there. tests/integration/test_mcp_generation_reset.py asserts the breaker never opens during a restart.

From review round 2 of PR #12 (narrow fix session, 2026-10-22; listed, not implemented):
- **The smoke-airgap upload check should also require the bytes sent below `max_file_bytes`**: scripts/refused_unread.sh fails only when the client had sent the whole body before the answer (ADR-0271), so a gateway that read part of the body and then answered the JSON 413 (a streaming size cap reading about 22 MB of the 30 MB file) would pass. Today's gateway refuses on Content-Length unread (about 1.1–1.5 MB sent), so it is a weakness of the check, not a live defect.
- **A database host that stops answering holds an admission connection past `admission_s` 10 s**: on the admission bound psycopg sends a cancel request (up to 5 s) and then waits for the cancelled query without a bound, so a database host that answers nothing (not a slow query: `pg_sleep` is cancelled at the bound, ADR-0264) keeps that pool connection until the host answers or the TCP connection fails. A whole-database outage, not one user's doing.

From phase 06 (make eval make-eval-20261024T190302Z, 2026-10-24; listed, not implemented):
- **A provider 429 inside a capability ends the turn `capability_failed`, not `model_unavailable`**: the capability's model call is refused by the provider, the isolation boundary reduces it to "raised TurnError" (ADR-0054) and the turn ends not retryable, so the eval harness's bounded backoff (which retries `model_unavailable` only) cannot retry it and the case is lost. Four directions cases of the skill suites were lost this way in that run. Carry the inner TurnError's code (data only, never the exception object) across the boundary, or retry the provider's 429 inside the capability's model caller.
- **"driving distance" is not a directions keyword**: an English request worded "the driving distance from A to B" makes no capability a router candidate and is answered by `general` with no route (the Arabic «المسافة» is a keyword). Two English skill cases were reworded to "how far"; the keyword list is the directions manifest's (routing for every capability is out of phase 06's scope).

## Management decisions pending (also in the phase summaries)
- Production model hosting on the entity's servers, same families as development (ADR-0065).
- Which topic policies to enable at go-live, and at which checkpoints (recommendation: INPUT only first).
- Memory retention.
- Whether entities need self-managed policies later (a tenant-admin role; ADR-0117 makes admin a platform operator).
- Whether external MCP servers (internet services) are ever allowed, and under which policy.
