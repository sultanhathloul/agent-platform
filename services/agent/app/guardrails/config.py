"""config/guardrails.yaml: the untrusted-content sanitizer (README §10.3) and the
DATA sentence buffer (D3, I5)."""

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SanitizeConfig(_Strict):
    max_chars: int = Field(gt=0)
    instruction_patterns: list[str] = []

    @field_validator("instruction_patterns")
    @classmethod
    def _compile(cls, patterns: list[str]) -> list[str]:
        for pattern in patterns:
            re.compile(pattern)
        return patterns


class StreamingConfig(_Strict):
    terminators: list[str] = Field(min_length=1)
    flush_chars: int = Field(gt=0)


class GuardrailsConfig(_Strict):
    sanitize: SanitizeConfig
    streaming: StreamingConfig


def load_guardrails_config(config_dir: Path) -> GuardrailsConfig:
    raw = yaml.safe_load((config_dir / "guardrails.yaml").read_text(encoding="utf-8"))
    return GuardrailsConfig.model_validate(raw)
