"""The skill registry at startup (phase 06, ADR-0295 to ADR-0300, ADR-0318): discovered, validated
against the capabilities, and each skill's block checked against its budget, so a bad skill fails
the container before the first turn."""

from collections.abc import Iterable, Sequence
from pathlib import Path

from app.context.config import ContextConfig
from app.context.package import SkillBlockRequest
from app.context.selectors.blocks import render_skill
from app.context.tokens import TokenCounter
from app.models.prompts import PromptLibrary
from app.skills.registry import SKILL_FILE, LoadedSkill, SkillError, SkillRegistry

LOCALES = ("ar", "en")


def discover_skills(root: Path, extra: Sequence[Path] = (), *, capabilities: Iterable[str]) -> SkillRegistry:
    """The skills of `root` and of any `extra` roots (test fixtures). A name used twice, or the name
    of a capability, fails; so does a required capability that does not exist."""
    capabilities = set(capabilities)
    loaded = []
    for found in (SkillRegistry.discover(path) for path in (root, *extra)):
        loaded += found.all()
    seen: dict[str, Path] = {}
    for skill in loaded:
        if skill.slug in seen:
            raise SkillError(f"skill {skill.slug!r} is defined twice: {seen[skill.slug]} and {skill.path}")
        if skill.slug in capabilities:
            raise SkillError(f"{skill.path}/{SKILL_FILE}: name {skill.slug!r} is a capability's slug")
        seen[skill.slug] = skill.path
    registry = SkillRegistry(loaded)
    registry.check_requires(capabilities)
    return registry


def block_request(skill: LoadedSkill) -> SkillBlockRequest:
    """The `skill` block's request for a skill: its name, version and instructions."""
    return SkillBlockRequest(slug=skill.slug, version=skill.manifest.version, instructions=skill.instructions)


def check_instruction_budgets(
    registry: SkillRegistry, prompts: PromptLibrary, config: ContextConfig, counter: TokenCounter
) -> dict[str, int]:
    """Each skill's rendered block, in each locale, within blocks.skill.max_tokens (ADR-0300,
    ADR-0318): the block is never cut, so an oversize skill fails startup. Returns each skill's
    largest block in tokens."""
    ceiling = config.blocks["skill"].max_tokens
    sizes: dict[str, int] = {}
    for skill in registry.all():
        for locale in LOCALES:
            text, _ = render_skill(prompts, block_request(skill), locale)
            tokens = counter.count(text)
            if tokens > ceiling:
                raise SkillError(
                    f"{skill.path}: the skill block is {tokens} tokens in {locale}, over "
                    f"blocks.skill.max_tokens {ceiling} (files {list(skill.loaded)})"
                )
            sizes[skill.slug] = max(tokens, sizes.get(skill.slug, 0))
    return sizes
