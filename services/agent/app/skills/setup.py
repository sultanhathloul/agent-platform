"""The skill registry at startup (phase 06, ADR-0295 to ADR-0300): discovered, validated against
the capabilities and the prompts, and each skill's instructions checked against its budget, so a
bad skill fails the container before the first turn."""

from collections.abc import Iterable, Sequence
from pathlib import Path

from app.context.config import ContextConfig
from app.context.package import SkillBlockRequest
from app.context.selectors.blocks import render_skill
from app.context.tokens import TokenCounter
from app.models.prompts import PromptLibrary
from app.skills.registry import PROMPTS_DIR, SKILL_FILE, SkillError, SkillRegistry

LOCALES = ("ar", "en")


def discover_skills(root: Path, extra: Sequence[Path] = (), *, capabilities: Iterable[str]) -> SkillRegistry:
    """The skills of `root` and of any `extra` roots (test fixtures). A slug used twice, or the slug
    of a capability, fails; so does a required capability that does not exist, or a prompt in a
    skill's folder other than its instructions in both locales."""
    capabilities = set(capabilities)
    loaded = []
    for found in (SkillRegistry.discover(path) for path in (root, *extra)):
        loaded += found.all()
    seen: dict[str, Path] = {}
    for skill in loaded:
        if skill.slug in seen:
            raise SkillError(f"skill {skill.slug!r} is defined twice: {seen[skill.slug]} and {skill.path}")
        if skill.slug in capabilities:
            raise SkillError(f"{skill.path}/{SKILL_FILE}: slug {skill.slug!r} is a capability's slug")
        seen[skill.slug] = skill.path
        own = PromptLibrary.load(skill.path / PROMPTS_DIR)
        wanted = skill.manifest.instructions_prompt
        others = sorted(own.ids() - {wanted})
        if others:
            raise SkillError(f"{skill.path}/{PROMPTS_DIR}: prompts {others}; a skill holds only {wanted!r}")
        try:
            own.require(wanted)
        except ValueError as exc:
            raise SkillError(f"{skill.path}/{PROMPTS_DIR}: {exc}") from exc
    registry = SkillRegistry(loaded)
    registry.check_requires(capabilities)
    return registry


def check_instruction_budgets(
    registry: SkillRegistry, prompts: PromptLibrary, config: ContextConfig, counter: TokenCounter
) -> None:
    """Each skill's rendered instructions, in each locale, within min(blocks.skill.max_tokens, its
    max_instruction_tokens) (ADR-0300): the block is never cut, so an oversize skill fails startup."""
    ceiling = config.blocks["skill"].max_tokens
    for skill in registry.all():
        limit = min(ceiling, skill.manifest.max_instruction_tokens)
        request = SkillBlockRequest(
            slug=skill.slug, version=skill.manifest.version, prompt=skill.manifest.instructions_prompt
        )
        for locale in LOCALES:
            try:
                text, _ = render_skill(prompts, request, locale)
            except Exception as exc:  # a template naming a variable: instructions take none
                raise SkillError(f"{skill.path}/{PROMPTS_DIR}: the {locale} instructions do not render: {exc}") from exc
            tokens = counter.count(text)
            if tokens > limit:
                raise SkillError(
                    f"{skill.path}/{PROMPTS_DIR}: the {locale} instructions are {tokens} tokens, over "
                    f"{limit} (min of blocks.skill.max_tokens {ceiling} and max_instruction_tokens "
                    f"{skill.manifest.max_instruction_tokens})"
                )
