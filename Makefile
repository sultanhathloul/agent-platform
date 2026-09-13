# Developer entry points (CLAUDE.md, "How to verify a phase").
SHELL := /bin/bash
.DEFAULT_GOAL := help
COMPOSE := docker compose
s ?=

.PHONY: help env models up down clean seed test test-agent test-gateway test-web eval smoke logs ps openapi

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-13s %s\n", $$1, $$2}'

env: .env ## Create .env with generated secrets (never overwrites)

.env:
	@scripts/init_env.sh

models: ## Pull the models named in infra/litellm/config.yaml into Ollama
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

test: test-agent test-gateway test-web ## Unit + integration tests; models via mock LiteLLM (ADR-0007)

test-agent: .env
	$(COMPOSE) --profile test up -d --wait litellm-mock
	$(COMPOSE) --profile test run --rm --build agent-tests

# --no-cache-filter test: the test stage runs on every call, never from a cached layer (ADR-0043).
# --build-context docs: the contract drift test compares docs/openapi/gateway.json (ADR-0044).
test-gateway:
	docker build --target test --no-cache-filter test --build-context docs=docs services/gateway

test-web:
	docker build --target test --no-cache-filter test services/web

eval: .env ## Golden suites against the live stack (real models)
	uv run evals/run_evals.py

smoke: .env ## End-to-end smoke test against the live stack
	scripts/smoke.sh

logs: ## Tail logs, e.g. make logs s=agent
	$(COMPOSE) logs -f --tail=200 $(s)

ps: ## Service status and health
	$(COMPOSE) ps --format 'table {{.Service}}\t{{.State}}\t{{.Health}}'

openapi: ## Regenerate docs/openapi/*.json, docs/events.schema.json and the web's API types
	cd services/agent && uv run python -m app.api.export
	docker run --rm -v "$(CURDIR)/services/gateway":/w -v gateway-gradle-cache:/home/gradle/.gradle -w /w \
		gradle:9.7.1-jdk21 gradle cleanTest test --no-daemon --no-build-cache --tests '*OpenApiExportTest.exportsSnakeCaseSpec'
	cp services/gateway/build/openapi/gateway.json docs/openapi/gateway.json
	cd services/web && npm run gen:api
