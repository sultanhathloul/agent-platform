from pathlib import Path

import pytest
import yaml
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.capabilities.contract import RuntimeContext
from app.capabilities.registry import (
    CapabilityRegistry,
    ManifestError,
    _compile_capability,
    load_manifest,
)
from app.identity import Principal

VALID = {
    "slug": "demo",
    "version": "1.0.0",
    "name": {"ar": "تجربة", "en": "Demo"},
    "description": {"ar": "وصف", "en": "Description"},
    "icon": "box",
    "permission": "cap:demo",
    "ui": {"mode": "toggle", "order": 10},
    "routing_hints": {"description": {"ar": "وصف", "en": "desc"}, "rules": [{"when": "keyword"}]},
    "model": {"min_tier": "fast"},
    "state_namespace": "cap.demo",
}


def _manifest(tmp_path, dirname="demo", **overrides):
    directory = tmp_path / dirname
    directory.mkdir()
    path = directory / "manifest.yaml"
    path.write_text(yaml.safe_dump({**VALID, **overrides}, allow_unicode=True), encoding="utf-8")
    return path


def test_valid_manifest_loads(tmp_path, models_config):
    assert load_manifest(_manifest(tmp_path), models_config).slug == "demo"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"permission": "tool:demo"}, "cap:demo"),
        ({"state_namespace": "demo"}, "cap.demo"),
        ({"model": {"min_tier": "turbo"}}, "unknown tier"),
        ({"model": {"min_tier": "fast", "steps": {"generate": "turbo"}}}, "unknown tier"),
        ({"context": {"needs": ["everything"]}}, "invalid manifest"),
        (
            {"routing_hints": {"description": {"ar": "و", "en": "d"}, "rules": [{"when": "always"}]}},
            "invalid manifest",
        ),
        ({"surprise": True}, "invalid manifest"),
    ],
)
def test_invalid_manifest_fails_fast_naming_the_file(tmp_path, models_config, overrides, message):
    path = _manifest(tmp_path, **overrides)
    with pytest.raises(ManifestError, match=message) as info:
        load_manifest(path, models_config)
    assert str(path) in str(info.value)


def test_slug_must_match_directory(tmp_path, models_config):
    with pytest.raises(ManifestError, match="must match directory"):
        load_manifest(_manifest(tmp_path, dirname="other"), models_config)


def test_discovers_general_and_filters_by_permission(models_config):
    registry = CapabilityRegistry.discover(models_config)
    assert "general" in [c.slug for c in registry.all()]
    with_role = Principal(user_id="u", permissions=("cap:general",))
    without = Principal(user_id="u", permissions=("kb:moe:read",))
    assert [c.slug for c in registry.available_for(with_role)] == ["general"]
    assert registry.available_for(without) == []


def _state_graph() -> StateGraph:
    graph = StateGraph(dict, context_schema=RuntimeContext)
    graph.add_node("run", lambda state: {})
    graph.add_edge(START, "run")
    graph.add_edge("run", END)
    return graph


def test_core_compiles_a_capability_state_graph():
    compiled = _compile_capability(_state_graph(), Path("demo"))
    assert isinstance(compiled, CompiledStateGraph)


class _AlreadyCompiled:
    """What build() must no longer return: a graph-like object the core did not
    compile (a compiled graph, or any object with an ainvoke). The core owns
    compilation, so anything but a StateGraph is refused (ADR-0053)."""

    checkpointer = None
    store = None

    async def ainvoke(self, *args, **kwargs):
        raise AssertionError("must never be invoked")


@pytest.mark.parametrize(
    "built",
    [_state_graph().compile(), _AlreadyCompiled(), object(), None],
    ids=["compiled_graph", "fake_graph", "plain_object", "none"],
)
def test_build_must_return_a_state_graph(built):
    with pytest.raises(ManifestError, match="must return a StateGraph"):
        _compile_capability(built, Path("demo"))
