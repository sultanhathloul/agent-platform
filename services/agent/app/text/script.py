"""Writing-script checks on generated text (ADR-0060, superseding ADR-0033).

For a locale with a script policy (config/validation.yaml), letters may come only
from the allowed Unicode scripts, and a whitespace-delimited token may not mix
letters of two scripts: `الريyal` fails, `الرياض (Riyadh)` passes, and a Cyrillic
or CJK letter fails anywhere. Digits, punctuation, symbols, whitespace and
combining or modifier marks carry no script, nor do the parts of an emoji sequence:
variation selectors (🏖️ and ⚙️ end in U+FE0F, a mark), skin-tone modifiers, the
zero-width joiner, keycaps, regional indicators and tags (F1, ADR-0340). Deterministic and model-free;
`validate` (README §5.2) and the answer node's retry use it.
"""

import unicodedata
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

DISALLOWED_SCRIPT = "disallowed_script"
MIXED_SCRIPT_TOKEN = "mixed_script_token"
ScriptIssue = Literal["disallowed_script", "mixed_script_token"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScriptPolicy(_Strict):
    letters: list[str] = Field(min_length=1, description="Unicode script names, e.g. ARABIC, LATIN.")
    mixed_token_allowlist: list[str] = []


class RetryPolicy(_Strict):
    issues: list[ScriptIssue]
    attempts: int = Field(ge=0, le=1)
    temperature: float = Field(ge=0.0, le=2.0)


class ValidationConfig(_Strict):
    scripts: dict[str, ScriptPolicy] = {}
    retry: RetryPolicy

    def scripts_for(self, locale: str) -> ScriptPolicy | None:
        return self.scripts.get(locale)


def load_validation_config(config_dir: Path) -> ValidationConfig:
    raw = yaml.safe_load((config_dir / "validation.yaml").read_text(encoding="utf-8"))
    return ValidationConfig.model_validate(raw)


def script_of(char: str) -> str | None:
    """The Unicode script of a letter or mark (the first word of its name), or None
    for a character that carries no script."""
    if unicodedata.category(char)[0] not in "LM":
        return None
    name = unicodedata.name(char, "")
    if not name or name.startswith(("COMBINING", "MODIFIER")) or "VARIATION SELECTOR" in name:
        return None
    return name.split(" ", 1)[0]


def _trimmed(token: str) -> str:
    start, end = 0, len(token)
    while start < end and script_of(token[start]) is None:
        start += 1
    while end > start and script_of(token[end - 1]) is None:
        end -= 1
    return token[start:end]


def script_issues(text: str, policy: ScriptPolicy) -> list[str]:
    allowed = set(policy.letters)
    allowlisted = set(policy.mixed_token_allowlist)
    disallowed = mixed = False
    for token in text.split():
        scripts = {script for char in token if (script := script_of(char))}
        if scripts - allowed:
            disallowed = True
        if len(scripts) > 1 and _trimmed(token) not in allowlisted:
            mixed = True
    return [code for code, found in ((DISALLOWED_SCRIPT, disallowed), (MIXED_SCRIPT_TOKEN, mixed)) if found]


def script_tokens(text: str, policy: ScriptPolicy) -> list[str]:
    """The words that fail the policy, each once, in order: what a retry's prompt names (ADR-0390)."""
    allowed = set(policy.letters)
    allowlisted = set(policy.mixed_token_allowlist)
    found: list[str] = []
    for token in text.split():
        scripts = {script for char in token if (script := script_of(char))}
        bad = scripts - allowed or (len(scripts) > 1 and _trimmed(token) not in allowlisted)
        if bad and _trimmed(token) not in found:
            found.append(_trimmed(token))
    return found
