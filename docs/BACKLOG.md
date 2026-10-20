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

### Owner's servers (after phase 05)
- Wire each by URL in `.env` (`url_env`, a bearer token in `bearer_env` when it needs one), add its entry and tools to `config/mcp.yaml` with the hashes `make mcp-hashes` prints, run the DocumentBackend contract suite (a RAG server) and the evals; settle the ICD table above with its team first.

### Deferred from phase 04 that bears on the MCP document backend
- → phase 05 (D7, ADR-0201: indexed with a `pages_without_text` warning the web shows; the pages themselves still wait for OCR). **Partly scanned PDFs**: a partly scanned PDF is indexed from its text pages only; its scanned pages are not read, and nothing tells the user. A PDF fails `scanned_unsupported` only when its text averages under `extraction.min_text_chars_per_page` per page over the whole file. OCR is v2, behind the `Extractor` hook. The OCR cases of scope item 4 should include a partly scanned PDF. (Source: PROGRESS phase 04, fix session round 1, R5; ADR-0128.)

---

## Skills → phase 06 (owner decision, 2026-10-14)

Status: not started. Done-check in `CLAUDE.md` (phase 06).

### Tech debt: first item of phase 06, before any new eval suite (owner decision, PR #10 review round 2, 2026-10-19)
- **Lost or interrupted cases count as failures in every metric.** Today rag and rag@mcp hit@8, citation page accuracy, faithfulness and answer correctness average only the cases that produced a result (`evals/run_evals.py` `rag_scores`, line 443 at 09a02a4): a case lost to `agent_stream_interrupted` or a provider error drops out of the denominator. Run make-eval-20261018T131835Z lost 7 rag cases and still passed. Every metric must score a lost case as a failure.

### What a skill is
- A skill describes **how** to produce an output or carry out a task: the form, the template, worked examples. It never says **where** information comes from; that stays in capabilities. Example: the docs capability finds the facts in an uploaded file; the "official memo" skill says how the memo is laid out (heading, reference, body, closing, signature block).
- Skills compose with any capability: the same memo skill works over docs, routing, kb, web or sql findings.
- A skill is a folder `skills/<slug>/SKILL.md`: name and description in Arabic and English, when to use it, the capabilities or MCP tools it needs, the Keycloak role that may use it. The router selects a capability and at most one skill; only the selected skill's instructions are loaded, as a reviewed procedural block.

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
- Some rag cases end in agent_stream_interrupted when rerank is slow (phase 04 Q1 runs); check stream timeouts against retrieval latency.
- Refused upload attempts (400 invalid path, 408, 413, 429) are not audited (ADR-0189 to ADR-0191 era); add rate-limited audit entries or metrics so abuse attempts are visible.
- Generic slow-request (slowloris) exposure on non-upload gateway paths: a slow body to any public path is read without the upload deadline or ceiling (PR #4 final verification, pre-existing). In production, a reverse proxy or ingress with request header and body timeouts in front of the gateway; or gateway-wide read deadlines.
- A model-provider 429 mid-stream ends the turn after the gateway's 60 s agent timeout (phase 04 final eval, routing docs-summary); review provider fallback and the timeout.

From phase 04 (added from PROGRESS and the ADRs, 2026-10-14):
- **Upload limits live in one gateway's memory**: the per-user rate and concurrency caps and the gateway-wide cap on uploads in flight are counted in the gateway process (one process in v1, ADR-0109). More than one gateway replica needs a shared counter (for example Redis). (Source: ADR-0185, ADR-0190.)
- **Development `.env` files created before ADR-0188** have no `UPLOADS_PER_MINUTE` and run at the strict 20 per minute, so `make eval` waits out 429s; copy `UPLOADS_PER_MINUTE=60` from `.env.example`. A note for the runbook. (Source: PROGRESS phase 04, narrow fix of 2026-10-14; ADR-0191.)
- **Docs latency not re-measured with rerank off**: the E3 measurements (time to indexed p50 2.42 s; docs answer TTFT p50 7.72 s, p95 8.13 s) were taken at the build, with the reranker on (16 candidates, first 400 tokens). The fix sessions measured `rag.retrieve` only (p50 0.13 s with rerank off). Re-run `scripts/measure_docs.py` for the production configuration. (Source: PROGRESS phase 04, E3 and the Q1 table; ADR-0184.)

From phase 05 (owner decision, fix session on PR #10, 2026-10-18; not implemented in phase 05):
- **Scoped LiteLLM keys per internal service**: `mcp-rag-ref` receives `LITELLM_MASTER_KEY`, following the existing precedent (the agent and the ingest service hold it too). Give each internal service a scoped LiteLLM key limited to the aliases it needs (the RAG server: `embed`, and `rerank` when it is on).

## Management decisions pending (also in the phase summaries)
- Production model hosting on the entity's servers, same families as development (ADR-0065).
- Which topic policies to enable at go-live, and at which checkpoints (recommendation: INPUT only first).
- Memory retention.
- Whether entities need self-managed policies later (a tenant-admin role; ADR-0117 makes admin a platform operator).
- Whether external MCP servers (internet services) are ever allowed, and under which policy.
