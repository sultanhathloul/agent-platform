"""config/guardrails.yaml: the untrusted-content sanitizer (README §10.3) and the
DATA sentence buffer (D3, I5); config/messages/sanitize.yaml: the sanitizer's marker
for a removed string, per locale."""

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SanitizeConfig(_Strict):
    max_chars: int = Field(gt=0)
    instruction_patterns: list[str] = []
    # From config/messages/sanitize.yaml: what replaces a removed string, per locale.
    removed_marker: dict[str, str] = Field(min_length=1)

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
    messages = yaml.safe_load((config_dir / "messages" / "sanitize.yaml").read_text(encoding="utf-8"))
    raw["sanitize"]["removed_marker"] = messages["removed_instruction"]
    return GuardrailsConfig.model_validate(raw)
