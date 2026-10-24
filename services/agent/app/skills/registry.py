"""Skill discovery (phase 06, ADR-0295 to ADR-0298). Every folder of the skills root is a skill,
validated at startup: a folder may hold only SKILL.md, prompts/ (prompt files, one per locale) and
evals/ (`<slug>.jsonl`, `metrics.yaml`); any other entry, or an executable bit, fails startup with
the path named, so no code can arrive as a skill (ADR-0291). The registry is in memory: listing
the skills a principal may use reads nothing outside the process (G7)."""

import re
import stat
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from app.identity import Principal
from app.skills.contract import SkillManifest
from app.text.arabic import contains_keyword

SKILL_FILE = "SKILL.md"
PROMPTS_DIR = "prompts"
EVALS_DIR = "evals"
METRICS_FILE = "metrics.yaml"
_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.S)
_PROMPT_FILE = re.compile(r"^[a-z0-9_.-]+\.md\.j2$")


class SkillError(RuntimeError):
    pass


@dataclass(frozen=True)
class LoadedSkill:
    manifest: SkillManifest
    path: Path

    @property
    def slug(self) -> str:
        return self.manifest.slug

    def matches(self, message: str) -> bool:
        """A keyword of the skill is in the message, matched Arabic-folded (app/text/arabic.py)."""
        hints = self.manifest.routing_hints
        return bool(hints.rules) and any(contains_keyword(message, k) for k in hints.keywords.all())


def _check_entry(path: Path) -> None:
    if path.is_symlink():
        raise SkillError(f"{path}: a skill holds no symbolic link")
    if path.is_file() and path.stat().st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
        raise SkillError(f"{path}: a skill file must not be executable (no scripts, ADR-0291)")


def _check_layout(directory: Path) -> None:
    """Only SKILL.md, prompts/*.md.j2 and evals/{<slug>.jsonl, metrics.yaml} (ADR-0295)."""
    slug = directory.name
    allowed_evals = {f"{slug}.jsonl", METRICS_FILE}
    for entry in sorted(directory.iterdir()):
        _check_entry(entry)
        if entry.name == SKILL_FILE and entry.is_file():
            continue
        if entry.name in (PROMPTS_DIR, EVALS_DIR) and entry.is_dir() and not entry.is_symlink():
            for inner in sorted(entry.iterdir()):
                _check_entry(inner)
                ok = inner.is_file() and (
                    _PROMPT_FILE.match(inner.name) if entry.name == PROMPTS_DIR else inner.name in allowed_evals
                )
                if not ok:
                    raise SkillError(
                        f"{inner}: not allowed in a skill's {entry.name}/ "
                        f"({'prompt files *.md.j2' if entry.name == PROMPTS_DIR else sorted(allowed_evals)})"
                    )
            continue
        raise SkillError(
            f"{entry}: not allowed in a skill folder; a skill holds only {SKILL_FILE}, "
            f"{PROMPTS_DIR}/ and {EVALS_DIR}/ (ADR-0295)"
        )
    if not (directory / SKILL_FILE).is_file():
        raise SkillError(f"{directory}: no {SKILL_FILE}")


def load_skill(directory: Path) -> LoadedSkill:
    _check_layout(directory)
    path = directory / SKILL_FILE
    match = _FRONT_MATTER.match(path.read_text(encoding="utf-8"))
    if not match:
        raise SkillError(f"{path}: missing YAML front-matter between --- lines")
    try:
        manifest = SkillManifest.model_validate(yaml.safe_load(match.group(1)) or {})
    except (ValidationError, yaml.YAMLError) as exc:
        raise SkillError(f"{path}: invalid SKILL.md\n{exc}") from exc
    if manifest.slug != directory.name:
        raise SkillError(f"{path}: slug {manifest.slug!r} must match directory {directory.name!r}")
    return LoadedSkill(manifest=manifest, path=directory)


class SkillRegistry:
    def __init__(self, skills: Iterable[LoadedSkill] = ()):
        self._by_slug = {s.slug: s for s in sorted(skills, key=lambda s: s.slug)}

    @classmethod
    def discover(cls, root: Path) -> "SkillRegistry":
        """Every folder under `root`; a missing root is no skill. Anything else at the root fails."""
        if not root.is_dir():
            return cls()
        loaded = []
        for entry in sorted(root.iterdir()):
            if not entry.is_dir() or entry.is_symlink():
                raise SkillError(f"{entry}: the skills root holds only skill folders (ADR-0295)")
            loaded.append(load_skill(entry))
        return cls(loaded)

    def get(self, slug: str) -> LoadedSkill | None:
        return self._by_slug.get(slug)

    def all(self) -> list[LoadedSkill]:
        return list(self._by_slug.values())

    def prompt_dirs(self) -> list[Path]:
        return [s.path / PROMPTS_DIR for s in self._by_slug.values() if (s.path / PROMPTS_DIR).is_dir()]

    def check_requires(self, capabilities: Iterable[str]) -> None:
        """Every capability a skill requires exists (fail fast at startup)."""
        known = set(capabilities)
        for skill in self._by_slug.values():
            unknown = sorted(set(skill.manifest.requires.capabilities) - known)
            if unknown:
                raise SkillError(f"{skill.path}/{SKILL_FILE}: requires.capabilities {unknown} are not capabilities")

    def available_for(self, principal: Principal, capabilities: Sequence[str]) -> list[LoadedSkill]:
        """The skills a turn may use (ADR-0298): the principal holds the skill's permission and every
        capability it requires is available to the principal (`capabilities`). TOOL_LIST policies
        filter a turn's skill afterwards, in guard(TOOL_LIST)."""
        available = set(capabilities)
        return [
            s
            for s in self._by_slug.values()
            if principal.has(s.manifest.permission) and set(s.manifest.requires.capabilities) <= available
        ]

    def candidates(self, available: Sequence[LoadedSkill], message: str) -> list[LoadedSkill]:
        """The available skills whose keywords are in the message: router-model candidates (ADR-0299)."""
        return [s for s in available if s.matches(message)]
