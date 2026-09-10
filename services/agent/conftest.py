"""Shared fixtures. Integration tests go through LiteLLM with mock aliases
(ADR-0007): `make test` starts `litellm-mock`; locally, run
`docker compose --profile test up -d litellm-mock` and pytest reaches it on
http://localhost:4001 with the master key from the repo .env."""

import os
from pathlib import Path

import httpx
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.settings import Settings, _default_config_dir

SPANS = InMemorySpanExporter()


def _repo_env() -> Path | None:
    """<repo>/.env in a checkout. The test container has no repo around
    /app/conftest.py; its settings come from the environment instead."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "docker-compose.yml").is_file():
            env = parent / ".env"
            return env if env.is_file() else None
    return None


def _dotenv(key: str) -> str | None:
    env = _repo_env()
    if env:
        for line in env.read_text().splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip('"')
    return None


def pytest_configure(config):
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(SPANS))
    trace.set_tracer_provider(provider)


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(
        litellm_base_url=os.environ.get("LITELLM_BASE_URL", "http://localhost:4001"),
        litellm_api_key=os.environ.get("LITELLM_API_KEY") or _dotenv("LITELLM_MASTER_KEY") or "",
        agent_internal_token="test-internal-token",
        config_dir=Path(os.environ.get("CONFIG_DIR") or _default_config_dir()),
        user_id_hash_salt="test-salt",
        observability_capture_content=True,
        otel_sdk_disabled=True,
    )


@pytest.fixture(scope="session")
def config_dir(settings) -> Path:
    return settings.config_dir


@pytest.fixture(scope="session")
def models_config(config_dir):
    from app.models.config import load_models_config

    return load_models_config(config_dir)


@pytest.fixture(scope="session")
def prompts(config_dir):
    from app.models.prompts import PromptLibrary

    return PromptLibrary.load(config_dir / "prompts")


@pytest.fixture(scope="session")
def litellm(settings) -> str:
    """Fails (never skips) when the mock LiteLLM is not reachable."""
    url = f"{settings.litellm_base_url}/health/liveliness"
    try:
        httpx.get(url, timeout=5).raise_for_status()
    except httpx.HTTPError as exc:
        hint = "docker compose --profile test up -d litellm-mock"
        pytest.fail(f"mock LiteLLM not reachable at {url} ({exc}); run: {hint}")
    return settings.litellm_base_url


@pytest.fixture(scope="session")
def services(settings):
    from app.main import build_services

    return build_services(settings)


@pytest.fixture
def spans():
    SPANS.clear()
    return SPANS


@pytest.fixture
async def client(services):
    from app.main import create_app

    app = create_app(start_background=False, services=services)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://agent") as http:
        yield http
