import pytest
import yaml

from app.capabilities.registry import CapabilityRegistry, ManifestError, load_manifest
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
