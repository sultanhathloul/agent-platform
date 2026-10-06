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


@lru_cache
def get_settings() -> Settings:
    return Settings()
