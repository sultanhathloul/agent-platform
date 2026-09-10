"""Capability discovery (README §18.4). Every package under app/capabilities/
with a manifest.yaml is loaded at startup. An invalid manifest or a missing
build() fails fast, naming the offending file."""

import importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from app.capabilities.contract import Manifest
from app.identity import Principal
from app.models.config import ModelsConfig
from app.models.routing import CapabilityModelSpec
from app.tools.spec import ToolSpec

PACKAGE_DIR = Path(__file__).resolve().parent
PACKAGE = "app.capabilities"


class ManifestError(RuntimeError):
    pass


@dataclass
class LoadedCapability:
    manifest: Manifest
    graph: Any  # CompiledStateGraph
    path: Path
    tools: list[ToolSpec] = field(default_factory=list)

    @property
    def slug(self) -> str:
        return self.manifest.slug

    def model_spec(self) -> CapabilityModelSpec:
        return self.manifest.model_spec()


def load_manifest(path: Path, models: ModelsConfig) -> Manifest:
    try:
        manifest = Manifest.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (ValidationError, yaml.YAMLError) as exc:
        raise ManifestError(f"{path}: invalid manifest\n{exc}") from exc
    if manifest.slug != path.parent.name:
        raise ManifestError(f"{path}: slug {manifest.slug!r} must match directory {path.parent.name!r}")
    for where, tier in [("model.min_tier", manifest.model.min_tier), *manifest.model.steps.items()]:
        if tier not in models.tiers:
            raise ManifestError(f"{path}: {where}: unknown tier {tier!r}; known: {models.order}")
    return manifest


class CapabilityRegistry:
    def __init__(self, capabilities: list[LoadedCapability]):
        self._by_slug = {c.slug: c for c in sorted(capabilities, key=lambda c: (c.manifest.ui.order, c.slug))}

    @classmethod
    def discover(
        cls, models: ModelsConfig, root: Path = PACKAGE_DIR, package: str = PACKAGE
    ) -> "CapabilityRegistry":
        loaded = []
        for directory in sorted(p for p in root.iterdir() if (p / "manifest.yaml").is_file()):
            manifest = load_manifest(directory / "manifest.yaml", models)
            module = importlib.import_module(f"{package}.{directory.name}.graph")
            build = getattr(module, "build", None)
            if not callable(build):
                raise ManifestError(f"{directory}/graph.py: missing build()")
            tools: list[ToolSpec] = []
            if (directory / "tools.py").is_file():
                tools = list(importlib.import_module(f"{package}.{directory.name}.tools").tools())
            loaded.append(LoadedCapability(manifest=manifest, graph=build(), path=directory, tools=tools))
        return cls(loaded)

    def get(self, slug: str) -> LoadedCapability:
        return self._by_slug[slug]

    def all(self) -> list[LoadedCapability]:
        return list(self._by_slug.values())

    def enabled(self) -> list[LoadedCapability]:
        # Runtime admin toggles (DB flag + Redis pub/sub) replace this default later.
        return [c for c in self._by_slug.values() if c.manifest.enabled_by_default]

    def available_for(self, principal: Principal) -> list[LoadedCapability]:
        return [c for c in self.enabled() if principal.has(c.manifest.permission)]

    def prompt_dirs(self) -> list[Path]:
        return [c.path / "prompts" for c in self._by_slug.values() if (c.path / "prompts").is_dir()]
