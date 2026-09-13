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


@lru_cache
def get_settings() -> Settings:
    return Settings()
