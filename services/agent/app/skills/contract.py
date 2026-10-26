"""The skill contract (phase 06, ADR-0289 to ADR-0308, ADR-0317 to ADR-0320). A skill is a folder
`skills/<name>/` at the repository root, mounted read-only like config/ (ADR-0295), in the open
Agent Skills format (ADR-0317): SKILL.md is YAML front-matter with `name` and `description`, then
Markdown instructions; the folder may hold further Markdown files that SKILL.md references. A
public skill loads unmodified. It shapes the answer's form and never says where information comes
from (ADR-0289); it holds no code (ADR-0291).

The platform's own fields are optional, in the namespaced front-matter block `sovereign`: the
version, the Arabic (and English) name and description shown in the picker, the permission, the
capabilities it requires and its routing keywords. Defaults: permission `skill:<name>`, no
required capability, no keyword (selectable by the picker only), labels from `name` and
`description`, a version from the loaded files' content.

The same fields may instead sit in a sidecar file `skill.platform.yaml` beside SKILL.md (ADR-0325),
so a skill copied unmodified gets Arabic labels, keywords and its permission without an edit to its
SKILL.md. A skill has one or the other, never both."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SKILL_PERMISSION_PREFIX = "skill:"
# The front-matter block holding the platform's fields; any other top-level key besides the open
# format's is kept in the file and ignored by the loader (it is listed in the startup log).
NAMESPACE = "sovereign"
# The sidecar holding the same fields as the namespaced block, for a skill whose SKILL.md stays
# untouched (ADR-0325).
PLATFORM_FILE = "skill.platform.yaml"
# A skill name: the open format's lowercase letters, digits and hyphens, and the underscore of the
# platform's own slugs; the folder carries the same name.
NAME_PATTERN = r"^[a-z][a-z0-9_-]{0,63}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Localized(_Strict):
    ar: str = Field(min_length=1)
    en: str = Field(min_length=1)


class OptionalLocalized(_Strict):
    ar: str | None = Field(default=None, min_length=1)
    en: str | None = Field(default=None, min_length=1)


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


class PlatformFields(_Strict):
    """The `sovereign` block (ADR-0317), or the sidecar `skill.platform.yaml` (ADR-0325). Any
    unknown key in it, `requires.mcp_tools` included, is refused at startup."""

    version: str | None = Field(default=None, pattern=r"^\d+\.\d+\.\d+$")
    name: OptionalLocalized = OptionalLocalized()
    description: OptionalLocalized = OptionalLocalized()
    permission: str | None = None
    requires: SkillRequires = SkillRequires()
    routing_hints: SkillRoutingHints = SkillRoutingHints()


class FrontMatter(BaseModel):
    """SKILL.md's front-matter: the open format's required `name` and `description`, the optional
    `sovereign` block, and any other key, kept and ignored."""

    model_config = ConfigDict(extra="allow", frozen=True, populate_by_name=True)

    name: str = Field(pattern=NAME_PATTERN)
    description: str = Field(min_length=1, max_length=1024)
    platform: PlatformFields = Field(default=PlatformFields(), alias=NAMESPACE)

    def declares_platform(self) -> bool:
        """The front-matter holds the namespaced block (even an empty one)."""
        return "platform" in self.model_fields_set

    def ignored_keys(self) -> list[str]:
        """Top-level keys the loader does not read (the open format's optional ones and others)."""
        return sorted(k for k in (self.model_extra or {}) if k != NAMESPACE)


class SkillManifest(_Strict):
    """What the platform reads from a skill, its defaults applied (ADR-0317)."""

    slug: str = Field(pattern=NAME_PATTERN)
    version: str = Field(min_length=1)
    name: Localized
    description: Localized
    permission: str
    requires: SkillRequires = SkillRequires()
    routing_hints: SkillRoutingHints = SkillRoutingHints()

    @model_validator(mode="after")
    def _derived_names(self) -> "SkillManifest":
        if self.permission != f"{SKILL_PERMISSION_PREFIX}{self.slug}":
            raise ValueError(f"permission must be 'skill:{self.slug}' (ADR-0297), got {self.permission!r}")
        if len(set(self.requires.capabilities)) != len(self.requires.capabilities):
            raise ValueError("requires.capabilities lists a capability twice")
        if self.routing_hints.rules and not self.routing_hints.keywords.all():
            raise ValueError("routing_hints.rules keyword_candidate needs keywords")
        return self

    @classmethod
    def of(
        cls, front: FrontMatter, content_version: str, platform: PlatformFields | None = None
    ) -> "SkillManifest":
        """The manifest of a front-matter; `platform` (the sidecar's fields) in place of its
        `sovereign` block when given; `content_version` when neither names a version."""
        own = platform if platform is not None else front.platform
        return cls(
            slug=front.name,
            version=own.version or content_version,
            name=Localized(ar=own.name.ar or front.name, en=own.name.en or front.name),
            description=Localized(
                ar=own.description.ar or front.description, en=own.description.en or front.description
            ),
            permission=own.permission or f"{SKILL_PERMISSION_PREFIX}{front.name}",
            requires=own.requires,
            routing_hints=own.routing_hints,
        )

    @property
    def ref(self) -> str:
        """`slug@version`, as traces and audit record the applied skill (G8)."""
        return f"{self.slug}@{self.version}"

    @property
    def policy_name(self) -> str:
        """The name TOOL_LIST policies see for this skill (ADR-0298)."""
        return f"{SKILL_PERMISSION_PREFIX}{self.slug}"
