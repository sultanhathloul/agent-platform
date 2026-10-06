"""Agent service entry point: `uvicorn app.main:create_app --factory`.

Everything is loaded and validated at startup (config, prompts, manifests), so
a bad file fails the container immediately instead of at the first turn."""

import dataclasses
import logging
import uuid
from collections.abc import Sequence
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.api.admin import router as admin_router
from app.api.locks import TurnLocks
from app.api.messages import ErrorMessages
from app.api.ownership import ConversationOwners
from app.api.routes import router
from app.api.runner import TurnRunner
from app.api.schemas import ErrorEnvelope
from app.capabilities.registry import CapabilityRegistry, ManifestError
from app.context.assembler import ContextAssembler, load_context_config
from app.context.log import ContextLog
from app.context.tokens import default_counter
from app.db import Database
from app.graph.build import build_graph
from app.graph.deps import Services
from app.graph.judge import GraphJudge
from app.guardrails.cache import RedisVerdictCache
from app.guardrails.checkers import (
    KeywordListChecker,
    LlmJudgeChecker,
    PresidioChecker,
    RegexChecker,
    TierCeilingChecker,
)
from app.guardrails.config import load_guardrails_config
from app.guardrails.engine import GuardEngine
from app.guardrails.sanitize import Sanitizer
from app.guardrails.store import PolicyStore
from app.memory.config import load_memory_config
from app.memory.retriever import MemoryRetriever
from app.memory.service import MemoryService
from app.memory.store import MemoryStore
from app.memory.writer import MemoryWriter
from app.models.config import load_models_config
from app.models.embeddings import EmbeddingGateway
from app.models.gateway import ModelGateway
from app.models.prompts import PromptLibrary
from app.models.routing import TierResolver
from app.observability.otel import setup_tracing, shutdown_tracing
from app.settings import Settings, get_settings
from app.text.script import load_validation_config
from app.tools.broker import ToolBroker
from app.tools.registry import ToolRegistry

CORE_PROMPTS = (
    "context.state_digest",
    "context.summary",
    "context.memory",
    "context.retrieval",
    "context.tool_result",
    "context.findings",
)

# Bump on any change to docs/openapi/agent.json; tests/unit/test_contracts.py fails on drift (ADR-0044).
API_VERSION = "1.3.0"


def build_services(settings: Settings, *, extra_capabilities: Sequence[tuple[Path, str]] = ()) -> Services:
    """`extra_capabilities` adds (directory, import package) roots discovered beside
    app/capabilities, e.g. the test-only capabilities under tests/fixtures."""
    models_config = load_models_config(settings.config_dir)
    registry = CapabilityRegistry.discover(models_config)
    if extra_capabilities:
        loaded = registry.all()
        for root, package in extra_capabilities:
            loaded += CapabilityRegistry.discover(models_config, root=root, package=package).all()
        registry = CapabilityRegistry(loaded)
    prompts = PromptLibrary.load(settings.config_dir / "prompts", *registry.prompt_dirs())
    context_config = load_context_config(settings.config_dir)
    memory_config = load_memory_config(settings.config_dir)

    node_prompts = [
        p for n in context_config.nodes.values() for p in (n.system_prompt, n.request_prompt) if p
    ]
    for prompt_id in (*CORE_PROMPTS, *node_prompts):
        prompts.require(prompt_id)
    for cap in registry.all():
        if "system" in cap.manifest.context.needs:
            try:
                prompts.require(f"{cap.slug}.system")
            except ValueError as exc:
                raise ManifestError(f"{cap.path}: context.needs has 'system' but {exc}") from exc

    counter = default_counter()
    db = Database(settings.database_url)
    redis = _redis(settings)
    guard_config = load_guardrails_config(settings.config_dir)
    policies = PolicyStore(db, redis, namespace=settings.redis_namespace)
    judge = GraphJudge()
    presidio = PresidioChecker(settings.presidio_url)
    cache = RedisVerdictCache(redis) if redis is not None else None
    checkers = {
        "regex": RegexChecker(),
        "keyword_list": KeywordListChecker(),
        "presidio": presidio,
        "llm_judge": LlmJudgeChecker(judge, cache, namespace=settings.redis_namespace),
        "tier_ceiling": TierCeilingChecker(models_config.order),
    }
    guard = GuardEngine(
        policies, checkers, tier_order=models_config.order, sanitizer=Sanitizer(guard_config.sanitize)
    )
    tools = ToolRegistry()
    for cap in registry.all():
        for spec in cap.tools:
            tools.register(spec, cap.slug)

    embeddings = EmbeddingGateway(settings, models_config)
    store = MemoryStore(db, memory_config)
    memory = MemoryService(
        config=memory_config,
        store=store,
        retriever=MemoryRetriever(store, embeddings, memory_config),
        writer=MemoryWriter(store, embeddings, memory_config),
    )
    services = Services(
        settings=settings,
        models_config=models_config,
        prompts=prompts,
        assembler=ContextAssembler(context_config, prompts, counter, memory=memory.retriever),
        resolver=TierResolver(models_config, guard),
        gateway=ModelGateway(settings, models_config, counter),
        embeddings=embeddings,
        guard=guard,
        registry=registry,
        tools=tools,
        broker=ToolBroker(tools, guard),
        messages=ErrorMessages.load(settings.config_dir),
        validation=load_validation_config(settings.config_dir),
        db=db,
        owners=ConversationOwners(db),
        memory=memory,
        context_log=ContextLog(
            db, enabled=settings.debug_context_enabled, retention_days=settings.debug_context_retention_days
        ),
        policies=policies,
        guard_config=guard_config,
        locks=TurnLocks(settings.database_url, size=settings.lock_pool_size),
        redis=redis,
        closers=(presidio.close,),
    )
    judge.bind(services)
    return services


def _redis(settings: Settings):
    if not settings.redis_url:
        return None
    from redis.asyncio import Redis

    return Redis.from_url(settings.redis_url, decode_responses=True)


def policy_roots(settings: Settings, registry: CapabilityRegistry) -> list[Path]:
    """Seed policies: config/policies/ and every capability's policies/ (README §18.1)."""
    return [settings.config_dir / "policies", *(cap.path / "policies" for cap in registry.all())]


async def start_services(services: Services) -> Services:
    """Opens the database, applies the agent's migrations and the checkpointer's, and
    compiles the main graph on the Postgres checkpointer (ADR-0055). The saver binds
    to the running event loop, so this runs inside it rather than in build_services."""
    if services.graph is not None:
        return services
    await services.db.open()
    checkpointer = AsyncPostgresSaver(services.db.pool)
    await checkpointer.setup()
    await services.locks.open()
    await services.policies.seed(policy_roots(services.settings, services.registry))
    await services.policies.start()
    return dataclasses.replace(services, graph=build_graph(checkpointer))


async def stop_services(services: Services) -> None:
    await services.policies.stop()
    for close in services.closers:
        await close()
    if services.redis is not None:
        await services.redis.aclose()
    await services.locks.close()
    await services.db.close()


def create_app(*, start_background: bool = True, services: Services | None = None) -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = services.settings if services else get_settings()
    services = services or build_services(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_tracing(enabled=start_background and not settings.otel_sdk_disabled)
        # Services handed in already started (tests) stay owned by their caller.
        owned = app.state.services.graph is None
        started = await start_services(app.state.services)
        app.state.services = started
        app.state.runner = TurnRunner(started)
        yield
        if owned:
            await stop_services(started)
        shutdown_tracing()

    app = FastAPI(
        title="Agent Platform Agent API (internal)",
        version=API_VERSION,
        description="Internal API called only by the gateway (README §6.2).",
        lifespan=lifespan,
    )
    app.openapi = lambda: _openapi(app)
    app.state.services = services
    app.state.runner = TurnRunner(services)
    app.include_router(router)
    app.include_router(admin_router)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        envelope = ErrorEnvelope.of(request_id, services.messages.error("invalid_request"))
        return JSONResponse(envelope.model_dump(), status_code=400)

    return app


def _openapi(app: FastAPI) -> dict:
    """FastAPI documents a 422 validation error on every operation with a body;
    this service answers invalid requests with the 400 error envelope instead."""
    if app.openapi_schema is None:
        schema = get_openapi(
            title=app.title, version=app.version, description=app.description, routes=app.routes
        )
        for operations in schema.get("paths", {}).values():
            for operation in operations.values():
                operation.get("responses", {}).pop("422", None)
        for unused in ("HTTPValidationError", "ValidationError"):
            schema.get("components", {}).get("schemas", {}).pop(unused, None)
        app.openapi_schema = schema
    return app.openapi_schema
