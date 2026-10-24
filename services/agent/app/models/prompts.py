"""Prompt loader (README §20, CLAUDE.md rule 5).

Prompt files live in config/prompts/ and app/capabilities/*/prompts/. This
module only finds, validates and renders them; it holds no prompt text.

A prompt file is `<name>.md.j2`: YAML front-matter between `---` lines, then a
Jinja2 template. Required front-matter: id, version, role, locale. A prompt is
addressed by (id, locale) and reported as `id@version`.
"""

import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from jinja2 import Environment, StrictUndefined, Template, TemplateSyntaxError

_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.S)
_REQUIRED = ("id", "version", "role", "locale")
_LOCALES = ("ar", "en")


class PromptError(ValueError):
    pass


@dataclass(frozen=True)
class RenderedPrompt:
    id: str
    version: str
    locale: str
    text: str

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"


@dataclass(frozen=True)
class _Entry:
    id: str
    version: str
    locale: str
    path: Path
    template: Template


def _environment() -> Environment:
    return Environment(undefined=StrictUndefined, autoescape=False, trim_blocks=True, lstrip_blocks=True)


def _parse(path: Path, env: Environment) -> _Entry:
    match = _FRONT_MATTER.match(path.read_text(encoding="utf-8"))
    if not match:
        raise PromptError(f"{path}: missing YAML front-matter")
    meta = yaml.safe_load(match.group(1)) or {}
    missing = [key for key in _REQUIRED if not meta.get(key)]
    if missing:
        raise PromptError(f"{path}: front-matter missing {', '.join(missing)}")
    if meta["locale"] not in _LOCALES:
        raise PromptError(f"{path}: locale must be one of {_LOCALES}")
    try:
        template = env.from_string(match.group(2))
    except TemplateSyntaxError as exc:
        raise PromptError(f"{path}:{exc.lineno}: {exc.message}") from exc
    return _Entry(str(meta["id"]), str(meta["version"]), meta["locale"], path, template)


class PromptLibrary:
    def __init__(self, entries: list[_Entry]):
        self._entries: dict[tuple[str, str], _Entry] = {}
        for entry in entries:
            key = (entry.id, entry.locale)
            if key in self._entries:
                first = self._entries[key].path
                raise PromptError(f"duplicate prompt {entry.id} ({entry.locale}): {first} and {entry.path}")
            self._entries[key] = entry

    @classmethod
    def load(cls, *roots: Path) -> "PromptLibrary":
        env = _environment()
        paths = sorted(p for root in roots if root.is_dir() for p in root.rglob("*.md.j2"))
        return cls([_parse(path, env) for path in paths])

    def ids(self) -> set[str]:
        """Every prompt id in the library (a skill's folder may hold only its instructions)."""
        return {prompt_id for prompt_id, _ in self._entries}

    def require(self, prompt_id: str) -> None:
        """Fails fast at startup when a prompt the code depends on is missing a locale."""
        missing = [locale for locale in _LOCALES if (prompt_id, locale) not in self._entries]
        if missing:
            raise PromptError(f"prompt {prompt_id} missing locale(s): {', '.join(missing)}")

    def ref(self, prompt_id: str, locale: str) -> str:
        """`id@version` of a prompt in a locale, e.g. for a cache key that must change with it."""
        entry = self._entries.get((prompt_id, locale))
        if entry is None:
            raise PromptError(f"no prompt {prompt_id} for locale {locale}")
        return f"{entry.id}@{entry.version}"

    def render(self, prompt_id: str, locale: str, **variables: object) -> RenderedPrompt:
        entry = self._entries.get((prompt_id, locale))
        if entry is None:
            raise PromptError(f"no prompt {prompt_id} for locale {locale}")
        text = entry.template.render(**variables).strip()
        return RenderedPrompt(entry.id, entry.version, entry.locale, text)
