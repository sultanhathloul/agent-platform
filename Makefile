# Developer entry points (CLAUDE.md, "How to verify a phase").
SHELL := /bin/bash
.DEFAULT_GOAL := help
COMPOSE := docker compose
s ?=

.PHONY: help env models up down clean seed mcp-hashes test test-agent test-gateway test-gateway-db test-web test-evals test-budget eval smoke smoke-airgap logs ps openapi purge-pre-guard

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-13s %s\n", $$1, $$2}'

env: .env ## Create .env with generated secrets (never overwrites)

.env:
	@scripts/init_env.sh

models: ## Pull the Ollama models named in .env and fetch the reranker weights (ADR-0134)
	@scripts/pull_models.sh

up: .env ## Build and start the full stack, seeded, and wait until healthy
	@scripts/preflight.sh
	$(COMPOSE) up -d --build --wait --wait-timeout 900

down: ## Stop the stack (keeps volumes)
	$(COMPOSE) --profile test down

clean: ## Stop the stack and delete every volume
	$(COMPOSE) --profile test down -v

seed: .env ## Re-run idempotent seed jobs (realm and Langfuse seed on first boot)
	$(COMPOSE) run --rm minio-init

test: test-agent test-gateway test-gateway-db test-web test-evals test-budget ## Unit + integration tests; models via mock LiteLLM (ADR-0007)

test-agent: .env
	$(COMPOSE) up -d --wait postgres redis presidio-analyzer minio
	$(COMPOSE) run --rm minio-init
	@# The test database and its owner come from infra/postgres/init; idempotent on an existing volume (ADR-0070).
	$(COMPOSE) exec -T postgres bash /docker-entrypoint-initdb.d/10-agent-test.sh
	@# The reference RAG server's role and its test database; CONNECT kept from PUBLIC (ADR-0242).
	$(COMPOSE) run --rm postgres-roles
	$(COMPOSE) --profile test up -d --wait litellm-mock
	@# --use-aliases: litellm-mock reaches the slow model provider the tests run, at agent-tests:8765 (ADR-0255).
	$(COMPOSE) --profile test run --rm --build --use-aliases agent-tests

# --no-cache-filter test: the test stage runs on every call, never from a cached layer (ADR-0043).
# --build-context docs: the contract drift test compares docs/openapi/gateway.json (ADR-0044).
# --build-context config: role tier caps are read from config/models.yaml (ADR-0046).
test-gateway:
	docker build --target test --no-cache-filter test --build-context docs=docs --build-context config=config services/gateway

# JDBC tests run as a container against the compose Postgres, not in the image build (ADR-0051).
test-gateway-db: .env
	$(COMPOSE) up -d --wait postgres
	$(COMPOSE) --profile test run --rm --build gateway-db-tests

# --build-context config: the config editors' tests read the seed policies (ADR-0123).
test-web:
	docker build --target test --no-cache-filter test --build-context config=config services/web

# RAGAS_DO_NOT_TRACK: no usage event leaves for Ragas' own service (S3); run_evals.py sets it too.
# The eval harness's own tests (S3): no call leaves the platform. Offline; the harness's script
# dependencies plus pytest.
test-evals:
	uv run --no-project --with pytest --with httpx --with pyyaml --with ragas==0.2.15 \
		--with 'langchain-openai>=0.2,<0.4' pytest -q evals/tests

# The timeout budget (I9, ADR-0261): every nested bound of docs/RESILIENCE.md shorter than the one
# around it, read from the files that set them; the self-test proves each check catches a violation.
test-budget:
	uv run -q scripts/check_timeout_budget.py --self-test
	uv run -q scripts/check_timeout_budget.py

eval: .env ## Golden suites against the live stack (real models)
	RAGAS_DO_NOT_TRACK=true uv run evals/run_evals.py $(s)

smoke: .env ## End-to-end smoke test against the live stack
	scripts/smoke.sh

# The stack on an internal network: no container but litellm reaches out (phase 04, F2, ADR-0147).
# Images and models must be present; the normal stack is restored afterwards, pass or fail.
AIRGAP_FILES := docker-compose.yml:docker-compose.airgap.yml
smoke-airgap: .env ## Smoke with egress blocked for every container except litellm
	@# Test-profile containers are not part of the stack under test; `make test` starts them again.
	$(COMPOSE) --profile test rm -sf litellm-mock embed-mock >/dev/null
	COMPOSE_FILE=$(AIRGAP_FILES) $(COMPOSE) up -d --wait --wait-timeout 900 --remove-orphans
	@status=0; \
	COMPOSE_FILE=$(AIRGAP_FILES) scripts/airgap_check.sh && COMPOSE_FILE=$(AIRGAP_FILES) scripts/smoke.sh || status=$$?; \
	$(COMPOSE) up -d --wait --wait-timeout 900 --remove-orphans >/dev/null; \
	exit $$status

purge-pre-guard: ## One-time purge of memory and summaries stored before the DATA guard (D4); DRY_RUN=1 only counts
	$(COMPOSE) exec -T agent python -m app.guardrails.purge $(if $(DRY_RUN),--dry-run,)

mcp-hashes: ## Print each MCP server's tool schema hashes beside config/mcp.yaml's pins (I5)
	$(COMPOSE) exec -T agent python -m app.mcp.hashes

logs: ## Tail logs, e.g. make logs s=agent
	$(COMPOSE) logs -f --tail=200 $(s)

ps: ## Service status and health
	$(COMPOSE) ps --format 'table {{.Service}}\t{{.State}}\t{{.Health}}'

openapi: ## Regenerate docs/openapi/*.json, docs/events.schema.json and the web's API types
	cd services/agent && uv run python -m app.api.export
	docker run --rm -v "$(CURDIR)/services/gateway":/w -v gateway-gradle-cache:/home/gradle/.gradle -w /w \
		-v "$(CURDIR)/config/models.yaml":/config/models.yaml:ro -e GATEWAY_MODELS_CONFIG=/config/models.yaml \
		-v "$(CURDIR)/config/docs.yaml":/config/docs.yaml:ro -e GATEWAY_DOCS_CONFIG=/config/docs.yaml \
		-v "$(CURDIR)/config/turn.yaml":/config/turn.yaml:ro -e GATEWAY_TURN_CONFIG=/config/turn.yaml \
		gradle:9.7.1-jdk21 gradle cleanTest test --no-daemon --no-build-cache --tests '*OpenApiExportTest.exportsSnakeCaseSpec'
	cp services/gateway/build/openapi/gateway.json docs/openapi/gateway.json
	cd services/web && npm run gen:api
