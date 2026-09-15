"""config/memory.yaml (README §8, ADR-0058)."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

# The kinds README §8 allows; the config may narrow them, never widen them.
MEMORY_KINDS = frozenset({"preference", "fact_about_user", "project_context", "decision"})


class MemoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled_by_default: bool
    kinds: list[str] = Field(min_length=1)
    min_confidence: float = Field(ge=0, le=1)
    max_items_per_turn: int = Field(ge=0)
    dedupe_similarity: float = Field(ge=0, le=1)
    min_similarity: float = Field(ge=-1, le=1)
    max_text_chars: int = Field(gt=0)
    ttl_days: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _known_kinds(self) -> "MemoryConfig":
        unknown = sorted(set(self.kinds) - MEMORY_KINDS)
        if unknown:
            raise ValueError(f"kinds: {unknown} are not memory kinds (README §8: {sorted(MEMORY_KINDS)})")
        return self


def load_memory_config(config_dir: Path) -> MemoryConfig:
    return MemoryConfig.model_validate(
        yaml.safe_load((config_dir / "memory.yaml").read_text(encoding="utf-8"))
    )
