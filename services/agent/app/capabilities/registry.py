"""Capability discovery (README §18.4). Every package under app/capabilities/
with a manifest.yaml is loaded at startup. An invalid manifest or a missing
build() fails fast, naming the offending file."""

import importlib
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from langgraph.graph.state import CompiledStateGraph, StateGraph
from pydantic import ValidationError

from app.capabilities.contract import Manifest
from app.capabilities.declarative import build_declarative
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
    graph: CompiledStateGraph  # compiled by the core from the capability's StateGraph (ADR-0053)
    path: Path
    tools: list[ToolSpec] = field(default_factory=list)
    # config/<slug>.yaml when it exists, handed to the capability in its RuntimeContext (C3).
    config: dict = field(default_factory=dict)

    @property
    def slug(self) -> str:
        return self.manifest.slug

    def model_spec(self) -> CapabilityModelSpec:
        return self.manifest.model_spec()


def _compile_capability(built: object, directory: Path) -> CompiledStateGraph:
    """The core owns compilation (ADR-0053). `build()` returns an uncompiled
    `StateGraph`; the core compiles it with no checkpointer, store or
    `interrupt_before/after`, so a capability cannot arrange its own persistence
    or interrupt the run. Any other return type is rejected here, before the
    graph is ever invoked."""
    if not isinstance(built, StateGraph):
        raise ManifestError(
            f"{directory}/graph.py: build() must return a StateGraph for the core to compile, "
            f"got {type(built).__name__}"
        )
    return built.compile()


def load_capability_config(config_dir: Path, slug: str, environ: Mapping[str, str] | None = None) -> dict:
    """A capability's own configuration file, config/<slug>.yaml, or nothing (C3). The document
    backend it names (`backend`, I1) may be overridden per environment by `<SLUG>_BACKEND` in
    .env (I11: `DOCS_BACKEND` for config/docs.yaml), so a deployment switches backend without a
    file edit."""
    path = config_dir / f"{slug}.yaml"
    loaded: object = {}
    if path.is_file():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ManifestError(f"{path}: a capability's configuration must be a mapping")
    config = dict(loaded)  # type: ignore[arg-type]
    override = (os.environ if environ is None else environ).get(f"{slug.upper()}_BACKEND", "").strip()
    if override:
        config["backend"] = override
    return config


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
            tools: list[ToolSpec] = []
            if manifest.declarative is not None:
                # A declarative capability is a manifest, prompts and evals: no Python (I12).
                python = sorted(p.name for p in directory.glob("*.py") if p.name != "__init__.py")
                if python:
                    raise ManifestError(
                        f"{directory}: a declarative capability has no Python file, found {python}"
                    )
                built = build_declarative(manifest, directory)
            else:
                module = importlib.import_module(f"{package}.{directory.name}.graph")
                build = getattr(module, "build", None)
                if not callable(build):
                    raise ManifestError(f"{directory}/graph.py: missing build()")
                if (directory / "tools.py").is_file():
                    tools = list(importlib.import_module(f"{package}.{directory.name}.tools").tools())
                built = build()
            graph = _compile_capability(built, directory)
            loaded.append(LoadedCapability(manifest=manifest, graph=graph, path=directory, tools=tools))
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
