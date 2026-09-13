"""Agent service entry point: `uvicorn app.main:create_app --factory`.

Everything is loaded and validated at startup (config, prompts, manifests), so
a bad file fails the container immediately instead of at the first turn."""

import logging
import uuid
from collections.abc import Sequence
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from app.api.messages import ErrorMessages
from app.api.routes import router
from app.api.runner import TurnRunner
from app.api.schemas import ErrorEnvelope
from app.capabilities.registry import CapabilityRegistry, ManifestError
from app.context.assembler import ContextAssembler, load_context_config
from app.context.tokens import default_counter
from app.graph.build import build_graph
from app.graph.deps import Services
from app.guardrails.engine import GuardEngine
from app.models.config import load_models_config
from app.models.gateway import ModelGateway
from app.models.prompts import PromptLibrary
from app.models.routing import TierResolver
from app.observability.otel import setup_tracing, shutdown_tracing
from app.settings import Settings, get_settings
from app.tools.broker import ToolBroker
from app.tools.registry import ToolRegistry

CORE_PROMPTS = ("context.state_digest", "context.findings")

# Bump on any change to docs/openapi/agent.json; tests/unit/test_contracts.py fails on drift (ADR-0044).
API_VERSION = "1.1.0"


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

    for prompt_id in (*CORE_PROMPTS, *(node.system_prompt for node in context_config.nodes.values())):
        prompts.require(prompt_id)
    for cap in registry.all():
        if "system" in cap.manifest.context.needs:
            try:
                prompts.require(f"{cap.slug}.system")
            except ValueError as exc:
                raise ManifestError(f"{cap.path}: context.needs has 'system' but {exc}") from exc

    counter = default_counter()
    guard = GuardEngine()
    tools = ToolRegistry()
    for cap in registry.all():
        for spec in cap.tools:
            tools.register(spec, cap.slug)

    return Services(
        settings=settings,
        models_config=models_config,
        prompts=prompts,
        assembler=ContextAssembler(context_config, prompts, counter),
        resolver=TierResolver(models_config, guard),
        gateway=ModelGateway(settings, models_config, counter),
        guard=guard,
        registry=registry,
        tools=tools,
        broker=ToolBroker(tools, guard),
        messages=ErrorMessages.load(settings.config_dir),
        graph=build_graph(),
    )


def create_app(*, start_background: bool = True, services: Services | None = None) -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = services.settings if services else get_settings()
    services = services or build_services(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        setup_tracing(enabled=start_background and not settings.otel_sdk_disabled)
        yield
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
