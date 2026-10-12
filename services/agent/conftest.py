"""Shared fixtures. Integration tests go through LiteLLM with mock aliases
(ADR-0007): `make test` starts `litellm-mock`; locally, run
`docker compose --profile test up -d litellm-mock` and pytest reaches it on
http://localhost:4001 with the master key from the repo .env.

State lives in Postgres (ADR-0055). The tests connect as the `app` role, like the
service, to the database `agent_test` that infra/postgres/init provisions with
the vector extension, and empty the agent's tables at the start of a session
instead of creating a database (ADR-0070). `make test` passes
AGENT_TEST_DATABASE_URL; locally, the dev overlay publishes Postgres on port 5433.

Guard policies are seeded from config/policies/ at service start, as in production.
Redis (database 15, a namespace per session) and presidio-analyzer are the real
services of the compose stack; `make test-agent` starts them."""

import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import psycopg
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
        content_hash_key="test-content-hash-key",
        observability_capture_content=True,
        otel_sdk_disabled=True,
        redis_url=os.environ.get("REDIS_URL") or f"redis://:{_dotenv('REDIS_PASSWORD') or ''}@localhost:6380/15",
        # A namespace per session: keys and channels never meet the stack's or another run's.
        redis_namespace=f"agent-test-{uuid.uuid4().hex[:8]}",
        presidio_url=os.environ.get("PRESIDIO_URL") or "http://localhost:5002",
        minio_endpoint=os.environ.get("MINIO_ENDPOINT") or "localhost:9000",
        # Tests store uploads as the gateway does and sweep as the ingest service does (S7).
        minio_access_key=os.environ.get("MINIO_ACCESS_KEY") or _dotenv("MINIO_GATEWAY_ACCESS_KEY") or "",
        minio_secret_key=os.environ.get("MINIO_SECRET_KEY") or _dotenv("MINIO_GATEWAY_SECRET_KEY") or "",
        minio_sweeper_access_key=os.environ.get("MINIO_SWEEPER_ACCESS_KEY") or _dotenv("MINIO_SWEEPER_ACCESS_KEY") or "",
        minio_sweeper_secret_key=os.environ.get("MINIO_SWEEPER_SECRET_KEY") or _dotenv("MINIO_SWEEPER_SECRET_KEY") or "",
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


def _test_database_url() -> str:
    if url := os.environ.get("AGENT_TEST_DATABASE_URL"):
        return url
    return f"postgresql://app:{_dotenv('APP_DB_PASSWORD') or ''}@localhost:5433/agent_test"


# Kept across sessions: what the migrations recorded, so a session does not re-apply them.
_KEEP = {"schema_migrations", "checkpoint_migrations"}


@pytest.fixture(scope="session")
def database_url() -> str:
    """The provisioned test database, its agent tables emptied. Fails (never skips)
    when Postgres or the database is unreachable."""
    url = _test_database_url()
    try:
        with psycopg.connect(url, autocommit=True, connect_timeout=5) as conn:
            tables = [
                row[0]
                for row in conn.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'agent'"
                ).fetchall()
                if row[0] not in _KEEP
            ]
            if tables:
                conn.execute(f"TRUNCATE {', '.join(f'agent.{t}' for t in tables)}")
    except psycopg.OperationalError as exc:
        hint = "make test-agent provisions it (infra/postgres/init/10-agent-test.sh)"
        pytest.fail(f"test database not reachable as the app role ({exc}); {hint}")
    return url


async def _started(settings: Settings, database_url: str, **kwargs):
    from app.main import build_services, start_services

    return await start_services(
        build_services(settings.model_copy(update={"database_url": database_url}), **kwargs)
    )


@pytest.fixture(scope="session")
async def services(settings, database_url) -> AsyncIterator:
    from app.main import stop_services

    started = await _started(settings, database_url)
    yield started
    await stop_services(started)


PROBE_CAPABILITIES = (
    Path(__file__).resolve().parent / "tests" / "fixtures" / "capabilities",
    "tests.fixtures.capabilities",
)


@pytest.fixture(scope="session")
async def probe_services(settings, database_url) -> AsyncIterator:
    """Services with the test-only `probe` capability discovered beside the real
    ones; no core file names it (ADR-0038)."""
    from app.main import stop_services

    started = await _started(settings, database_url, extra_capabilities=[PROBE_CAPABILITIES])
    yield started
    await stop_services(started)


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
