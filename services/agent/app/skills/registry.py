"""Skill discovery (phase 06, ADR-0295 to ADR-0298, ADR-0317, ADR-0318). Every folder of the skills
root is a skill in the open Agent Skills format, validated at startup:
- a script (by its suffix or a `#!` first line), an executable bit or a symbolic link anywhere in
  the folder fails startup with the path named, so no code can arrive as a skill (ADR-0291, D3);
- the instructions are SKILL.md's body plus every Markdown file of the folder that the body names
  by its relative path, loaded together, in the order the body first names them (ADR-0318);
- every other file (a licence, a SOURCE note, a Markdown file the body does not name, the evals/
  folder's data) stays in the folder, is skipped and never reaches the model; the startup log
  lists it.
The registry is in memory: listing the skills a principal may use reads nothing outside the
process (G7)."""

import hashlib
import logging
import re
import stat
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from app.identity import Principal
from app.skills.contract import FrontMatter, SkillManifest
from app.text.arabic import contains_keyword

SKILL_FILE = "SKILL.md"
EVALS_DIR = "evals"
METRICS_FILE = "metrics.yaml"
MARKDOWN_SUFFIX = ".md"
# Files that are code by their suffix; a `#!` first line marks one too (ADR-0318).
SCRIPT_SUFFIXES = frozenset({
    ".py", ".pyc", ".pyw", ".sh", ".bash", ".zsh", ".fish", ".ksh", ".csh", ".ps1", ".psm1", ".bat",
    ".cmd", ".com", ".exe", ".dll", ".so", ".dylib", ".bin", ".js", ".mjs", ".cjs", ".ts", ".mts",
    ".cts", ".jsx", ".tsx", ".rb", ".pl", ".pm", ".php", ".lua", ".r", ".jar", ".class", ".wasm",
    ".vbs", ".applescript", ".scpt", ".swift", ".go", ".rs", ".c", ".cc", ".cpp", ".java", ".kt",
})
# A skill file is a reviewed text file; one past this size fails startup before it is read.
MAX_FILE_BYTES = 256 * 1024
_FRONT_MATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.S)

log = logging.getLogger(__name__)


class SkillError(RuntimeError):
    pass


@dataclass(frozen=True)
class LoadedSkill:
    manifest: SkillManifest
    path: Path
    # SKILL.md's body and the Markdown files it names, as the `skill` block renders them.
    instructions: str = ""
    # The files loaded into the instructions and those skipped, relative to the folder.
    loaded: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()

    @property
    def slug(self) -> str:
        return self.manifest.slug

    def matches(self, message: str) -> bool:
        """A keyword of the skill is in the message, matched Arabic-folded (app/text/arabic.py)."""
        hints = self.manifest.routing_hints
        return bool(hints.rules) and any(contains_keyword(message, k) for k in hints.keywords.all())


def _is_script(path: Path) -> bool:
    if path.suffix.lower() in SCRIPT_SUFFIXES:
        return True
    with path.open("rb") as handle:
        return handle.read(2) == b"#!"


def _files(directory: Path) -> list[Path]:
    """Every file of the folder, checked: no symbolic link, no executable bit, no script (D3)."""
    found = []
    for entry in sorted(directory.rglob("*")):
        if entry.is_symlink():
            raise SkillError(f"{entry}: a skill holds no symbolic link")
        if entry.is_dir():
            continue
        mode = entry.stat().st_mode
        if not stat.S_ISREG(mode):
            raise SkillError(f"{entry}: a skill holds only regular files")
        if mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
            raise SkillError(f"{entry}: a skill file must not be executable (no scripts, ADR-0291)")
        if _is_script(entry):
            raise SkillError(f"{entry}: a skill holds no script (ADR-0291, ADR-0318)")
        if entry.stat().st_size > MAX_FILE_BYTES:
            raise SkillError(f"{entry}: over {MAX_FILE_BYTES} bytes")
        found.append(entry)
    return found


def _named(body: str, candidates: Iterable[str]) -> list[str]:
    """The candidate paths the body names, in the order it first names them."""
    positions = {}
    for rel in candidates:
        # The whole path, not a part of a longer one (`a.md` is not named by `examples/a.md`).
        found = re.search(rf"(?<![\w./-]){re.escape(rel)}(?![\w/-])", body)
        if found:
            positions[rel] = found.start()
    return sorted(positions, key=positions.__getitem__)


def compose(body: str, references: Sequence[tuple[str, str]]) -> str:
    """SKILL.md's body, then each named file delimited by its path."""
    parts = [body.strip()]
    parts += [f'<file path="{rel}">\n{text.strip()}\n</file>' for rel, text in references]
    return "\n\n".join(parts)


def load_skill(directory: Path) -> LoadedSkill:
    files = _files(directory)
    path = directory / SKILL_FILE
    if path not in files:
        raise SkillError(f"{directory}: no {SKILL_FILE}")
    match = _FRONT_MATTER.match(path.read_text(encoding="utf-8"))
    if not match:
        raise SkillError(f"{path}: missing YAML front-matter between --- lines")
    try:
        front = FrontMatter.model_validate(yaml.safe_load(match.group(1)) or {})
    except (ValidationError, yaml.YAMLError) as exc:
        raise SkillError(f"{path}: invalid SKILL.md\n{exc}") from exc
    if front.name != directory.name:
        raise SkillError(f"{path}: name {front.name!r} must match directory {directory.name!r}")
    body = match.group(2)
    relative = [f.relative_to(directory) for f in files]
    markdown = [
        rel.as_posix()
        for rel in relative
        if rel.as_posix() != SKILL_FILE and rel.suffix.lower() == MARKDOWN_SUFFIX
        and rel.parts[0] != EVALS_DIR
    ]
    named = _named(body, markdown)
    references = [(rel, (directory / rel).read_text(encoding="utf-8")) for rel in named]
    loaded = (SKILL_FILE, *named)
    skipped = tuple(rel.as_posix() for rel in relative if rel.as_posix() not in loaded)
    digest = hashlib.sha256()
    for rel in loaded:
        digest.update(rel.encode() + b"\0" + (directory / rel).read_bytes() + b"\0")
    try:
        manifest = SkillManifest.of(front, content_version=f"sha-{digest.hexdigest()[:12]}")
    except ValidationError as exc:
        raise SkillError(f"{path}: invalid SKILL.md\n{exc}") from exc
    # The startup log lists what reaches the model and what never does (ADR-0318).
    log.info(
        "skill %s loaded=%s skipped=%s ignored_front_matter=%s",
        manifest.ref, list(loaded), list(skipped), front.ignored_keys(),
    )
    return LoadedSkill(
        manifest=manifest, path=directory, instructions=compose(body, references),
        loaded=loaded, skipped=skipped,
    )


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


def available_skills(services, principal: Principal) -> list[LoadedSkill]:
    """The skills a principal may use (ADR-0298): its permission held, and every capability it
    requires available to the principal (enabled and permitted). `services`: app.graph.deps.Services."""
    capabilities = [c.slug for c in services.registry.available_for(principal)]
    return services.skills.available_for(principal, capabilities)
