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

## Decided

Choices confirmed by the owner.

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

## Assumed

Places where the spec was silent. We picked the simplest option that fits the architecture, and the owner may overturn it.

_No entries yet._

## Proposed

Arguments against a closed decision. The current decision stays in force until the owner accepts a proposal.

_No entries yet._
