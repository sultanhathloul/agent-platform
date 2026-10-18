"""Service settings, read from the environment (compose sets them; see .env.example)."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_config_dir() -> Path:
    """<repo>/config when running from a checkout; /srv/config (compose mount) otherwise."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "config" / "models.yaml").is_file():
            return parent / "config"
    return Path("/srv/config")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    litellm_base_url: str = "http://localhost:4000"
    litellm_api_key: str = ""
    agent_internal_token: str = ""
    config_dir: Path = Field(default_factory=_default_config_dir)
    user_id_hash_salt: str = ""
    # Key of the HMAC-SHA256 content hashes in traces (D5, ADR-0110); the agent refuses to start without it.
    content_hash_key: str = ""
    observability_capture_content: bool = True
    otel_sdk_disabled: bool = False
    model_timeout_s: float = 300.0
    # libpq connection string of the `app` database (ADR-0055); compose builds it from APP_DB_PASSWORD.
    database_url: str = ""
    # GET /v1/debug/context/{turn_id}, and recording the packages it returns (ADR-0059). Off by default.
    debug_context_enabled: bool = False
    # Recorded context packages expire after this many days (D5).
    debug_context_retention_days: int = Field(default=7, ge=1)
    # Policy cache, change notifications and judge verdicts (README §10.1, I3, I4). Empty: no Redis.
    redis_url: str = ""
    # Prefix of every Redis key and channel, so test runs never meet the running stack's.
    redis_namespace: str = "agent"
    # presidio-analyzer with the Saudi recognizers of infra/presidio/ (README §10.2).
    presidio_url: str = "http://localhost:5002"
    # Dedicated connections holding per-conversation turn locks (D6).
    lock_pool_size: int = Field(default=20, ge=1)
    # Object storage of uploads (phase 04, ADR-0129): MinIO on the compose network. The ingest
    # service reads uploads with this key; its orphan sweeper lists and removes unregistered
    # objects with the sweeper key (owner decisions R4, S4, S7).
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_sweeper_access_key: str = ""
    minio_sweeper_secret_key: str = ""
    minio_secure: bool = False
    uploads_bucket: str = "uploads"
    # The ingest service's health port (compose healthcheck).
    ingest_health_port: int = 8001
    # The reranker, per environment (review round 2, finding 3): development leaves it off; a GPU
    # deployment turns it on with 16 candidates on 1070 characters. These override config/docs.yaml's
    # retrieval.rerank, so a redeploy changes the configuration without a code change (see .env.example).
    rerank_enabled: bool = False
    rerank_candidates: int = Field(default=16, gt=0)
    rerank_max_chars: int | None = Field(default=1070, gt=0)
    # MCP (phase 05): the allowlist file (default config/mcp.yaml), the profile this deployment runs
    # (a server is enabled when it is in the server's `profiles`; `airgap` never enables an external
    # server), and whether external servers may be enabled at all (I16, off by default).
    mcp_config: Path | None = None
    mcp_profile: str = "default"
    mcp_allow_external: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
