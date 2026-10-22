"""The files' values a settings store starts from, for the agent and the ingest service alike."""

import logging
from pathlib import Path

import yaml

from app.attachments.limits import UploadLimits
from app.mcp.config import load_mcp_config
from app.models.config import ModelsConfig
from app.platform_settings.effective import Defaults
from app.resilience import budget, budget_values
from app.resilience.turn import TurnConfig
from app.settings import Settings

log = logging.getLogger(__name__)
APP_DIR = Path(__file__).resolve().parents[1]


def mcp_path(settings: Settings) -> Path:
    return settings.mcp_config or settings.config_dir / "mcp.yaml"


def build_defaults(
    settings: Settings,
    *,
    turn: TurnConfig,
    models_config: ModelsConfig,
    upload: UploadLimits,
    register_response_s: float,
) -> Defaults:
    """The budget rules' values from the files (the same loader as scripts/check_timeout_budget.py,
    ADR-0277), with what this deployment's environment sets instead: the MCP allowlist it runs,
    UPLOADS_PER_MINUTE, and LOCK_POOL_SIZE for the ranges the platform's capacity bounds. The
    objects the agent built from the same files (already validated) are kept beside them."""
    values = budget_values.load_values(settings.config_dir, APP_DIR)
    path = mcp_path(settings)
    load_mcp_config(path)  # validated as the client loads it
    values["mcp"] = yaml.safe_load(path.read_text(encoding="utf-8"))
    per_minute = settings.docs_uploads_per_minute or int(values["docs"]["limits"]["uploads_per_minute"])
    values["docs"]["limits"]["uploads_per_minute"] = per_minute
    values["platform"] = {"lock_pool_size": settings.lock_pool_size}
    found, missing = budget.verify(values)
    if failed := [check.name for check in found if not check.holds] + missing:
        # make test runs the same rules on the same files and is the gate; here the files' own
        # problems are logged, and every save is refused until they are fixed (ADR-0277).
        log.error("the timeout budget fails on the files: %s", ", ".join(failed))
    return Defaults(
        values=values,
        turn=turn,
        timeouts=models_config.timeouts,
        upload=upload,
        register_response_s=register_response_s,
        uploads_per_minute=per_minute,
        mcp_max_concurrency={s["id"]: int(s["max_concurrency"]) for s in values["mcp"]["servers"]},
        lock_pool_size=settings.lock_pool_size,
        deploy_sources=dict(budget_values.load_deploy(settings.config_dir).get("sources") or {}),
    )
