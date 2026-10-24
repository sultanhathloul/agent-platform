"""The skill contract (phase 06, ADR-0289 to ADR-0308). A skill is a folder `skills/<slug>/` at
the repository root, mounted read-only like config/ (ADR-0295). It shapes the answer's form and
never says where information comes from (ADR-0289); it holds no code (ADR-0291).

SKILL.md is YAML front-matter between `---` lines, then a body of notes for reviewers that is
never loaded (ADR-0296). The instructions are prompt files under prompts/, one per locale, with
the id `<slug>.instructions`; the answer node loads only the selected skill's, as the `skill`
block of its context package (ADR-0300)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SKILL_PERMISSION_PREFIX = "skill:"
# The prompt id of a skill's instructions; the registry requires both locales.
INSTRUCTIONS = "instructions"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Localized(_Strict):
    ar: str = Field(min_length=1)
    en: str = Field(min_length=1)


class LocalizedKeywords(_Strict):
    ar: tuple[str, ...] = ()
    en: tuple[str, ...] = ()

    def all(self) -> tuple[str, ...]:
        return (*self.ar, *self.en)


class SkillRule(_Strict):
    # keyword_candidate: a keyword in the message makes the skill a router-model candidate; it
    # never selects it (ADR-0216, ADR-0299). The only rule a skill may declare.
    when: Literal["keyword_candidate"]


class SkillRoutingHints(_Strict):
    keywords: LocalizedKeywords = LocalizedKeywords()
    rules: tuple[SkillRule, ...] = ()


class SkillRequires(_Strict):
    # The capabilities the skill needs, all of them available to the principal; [] composes with
    # any capability. Never MCP tools: a tool is reached only through its capability (ADR-0307).
    capabilities: tuple[str, ...] = ()


class SkillManifest(_Strict):
    """SKILL.md's front-matter (ADR-0296). Any unknown key, `requires.mcp_tools` included, is
    refused at startup."""

    slug: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    name: Localized
    description: Localized
    when_to_use: Localized
    permission: str
    requires: SkillRequires = SkillRequires()
    routing_hints: SkillRoutingHints = SkillRoutingHints()
    max_instruction_tokens: int = Field(gt=0)

    @model_validator(mode="after")
    def _derived_names(self) -> "SkillManifest":
        if self.permission != f"{SKILL_PERMISSION_PREFIX}{self.slug}":
            raise ValueError(f"permission must be 'skill:{self.slug}' (ADR-0297), got {self.permission!r}")
        if len(set(self.requires.capabilities)) != len(self.requires.capabilities):
            raise ValueError("requires.capabilities lists a capability twice")
        if self.routing_hints.rules and not self.routing_hints.keywords.all():
            raise ValueError("routing_hints.rules keyword_candidate needs keywords")
        return self

    @property
    def ref(self) -> str:
        """`slug@version`, as traces and audit record the applied skill (G8)."""
        return f"{self.slug}@{self.version}"

    @property
    def instructions_prompt(self) -> str:
        return f"{self.slug}.{INSTRUCTIONS}"

    @property
    def policy_name(self) -> str:
        """The name TOOL_LIST policies see for this skill (ADR-0298)."""
        return f"{SKILL_PERMISSION_PREFIX}{self.slug}"
