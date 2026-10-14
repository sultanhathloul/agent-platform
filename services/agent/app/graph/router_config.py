"""config/routing.yaml: the router model's window and cache lifetime (ADR-0139)."""

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class RouterConfig:
    window_messages: int
    cache_ttl_s: int


def load_router_config(config_dir: Path) -> RouterConfig:
    raw = yaml.safe_load((config_dir / "routing.yaml").read_text(encoding="utf-8"))["router"]
    return RouterConfig(window_messages=int(raw["window_messages"]), cache_ttl_s=int(raw["cache_ttl_s"]))
