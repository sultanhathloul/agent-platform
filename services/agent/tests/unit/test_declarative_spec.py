"""The declarative manifest section (phase 05, G2, I12): references resolve to the extract schema or
an earlier step, steps call only declared tools, prompts are the capability's own, the extract
schema is an object schema; a declarative capability holds no Python. The runner's mapping is
deterministic. Also the keyword_candidate routing rule (I15): a keyword makes a candidate, never
a selection."""

from pathlib import Path

import pytest
import yaml

from app.capabilities.contract import Manifest
from app.capabilities.declarative import _arguments, _candidates, _Missing, _resolve
from app.capabilities.registry import CapabilityRegistry, LoadedCapability, ManifestError
from app.graph.nodes.route import router_candidates, select_capabilities

WAYPOINT = Path(__file__).resolve().parents[1] / "fixtures" / "capabilities" / "waypoint" / "manifest.yaml"


def _manifest(**declarative) -> dict:
    raw = yaml.safe_load(WAYPOINT.read_text(encoding="utf-8"))
    raw["declarative"] = {**raw["declarative"], **declarative}
    return raw


def test_the_test_only_manifest_is_declarative_and_valid():
    manifest = Manifest.model_validate(yaml.safe_load(WAYPOINT.read_text(encoding="utf-8")))
    assert manifest.declarative is not None
    assert [s.id for s in manifest.declarative.steps] == ["first", "second"]
    assert not list(WAYPOINT.parent.glob("*.py"))


@pytest.mark.parametrize(
    "steps, error",
    [
        ([{"id": "a", "tool": "probe_lookup", "args": {"text": "$.extract.nowhere"}}], "names no field"),
        ([{"id": "a", "tool": "probe_lookup", "args": {"text": "$.steps.b.result.x"}}], "no earlier step"),
        ([{"id": "a", "tool": "probe_lookup", "args": {"text": "$.extract.place + 1"}}], "is not"),
        ([{"id": "a", "tool": "probe_lookup", "args": {"text": "$..place"}}], "is not"),
        (
            [
                {"id": "a", "tool": "probe_lookup", "args": {}},
                {"id": "a", "tool": "probe_lookup", "args": {}},
            ],
            "used twice",
        ),
        ([{"id": "a", "tool": "docs_search", "args": {"text": "$.extract.place"}}], "does not declare"),
    ],
)
def test_invalid_steps_are_refused(steps, error):
    with pytest.raises(ValueError, match=error):
        Manifest.model_validate(_manifest(steps=steps))


def test_prompts_are_the_capabilitys_own_and_the_schema_is_an_object():
    with pytest.raises(ValueError, match="own"):
        Manifest.model_validate(_manifest(findings={"prompt": "docs.evidence"}))
    raw = _manifest()
    raw["declarative"]["extract"]["schema"] = {"type": "string"}
    with pytest.raises(ValueError, match="object schema"):
        Manifest.model_validate(raw)


def test_a_declarative_capability_with_a_python_file_is_refused(tmp_path, models_config):
    package = tmp_path / "caps"
    capability = package / "waypoint"
    (capability / "prompts").mkdir(parents=True)
    (capability / "manifest.yaml").write_text(WAYPOINT.read_text(encoding="utf-8"), encoding="utf-8")
    for prompt in (WAYPOINT.parent / "prompts").iterdir():
        (capability / "prompts" / prompt.name).write_text(
            prompt.read_text(encoding="utf-8"), encoding="utf-8"
        )
    (capability / "graph.py").write_text("def build(): ...\n", encoding="utf-8")
    with pytest.raises(ManifestError, match="no Python"):
        CapabilityRegistry.discover(models_config, root=package, package="caps")
    (capability / "graph.py").unlink()
    loaded = CapabilityRegistry.discover(models_config, root=package, package="caps")
    assert [c.slug for c in loaded.all()] == ["waypoint"]


def test_references_resolve_deterministically():
    extract = {"origin": "برج المملكة", "destination": "", "profile": None}
    results = {"from": {"best": {"id": "kingdom_centre"}, "matches": [{"id": "a"}, {"id": "b"}]}}
    assert _resolve("$.extract.origin", extract, results) == "برج المملكة"
    assert _resolve("$.steps.from.result.best.id", extract, results) == "kingdom_centre"
    assert _resolve("$.steps.from.result.matches.1.id", extract, results) == "b"
    assert _resolve("car", extract, results) == "car"
    for missing in ("$.extract.destination", "$.steps.to.result.best.id", "$.steps.from.result.matches.9.id"):
        with pytest.raises(_Missing):
            _resolve(missing, extract, results)
    args = _arguments(
        {"query": "$.extract.origin", "profile": "$.extract.profile"}, extract, results, {"$.extract.profile"}
    )
    assert args == {"query": "برج المملكة"}  # an empty optional field leaves its argument out
    many = {"from": {"matches": [{"id": i} for i in range(5)]}}
    assert len(_candidates(many, "from")) == 3


# ------------------------------------------------------------------ keyword_candidate (I15)


def _loaded(slug: str, rules: list[str], keywords=()) -> LoadedCapability:
    raw = yaml.safe_load(WAYPOINT.read_text(encoding="utf-8"))
    raw.update(slug=slug, permission=f"cap:{slug}", state_namespace=f"cap.{slug}", tools=[], declarative=None)
    raw["routing_hints"]["rules"] = [{"when": r} for r in rules]
    raw["routing_hints"]["keywords"] = {"ar": list(keywords), "en": []}
    return LoadedCapability(manifest=Manifest.model_validate(raw), graph=None, path=Path("."))  # type: ignore[arg-type]


def test_a_keyword_makes_a_router_candidate_and_never_selects():
    way = _loaded("way", ["keyword_candidate"], ["ودني", "طريق"])
    general = _loaded("general", ["fallback"])
    selected, reasons = select_capabilities([way, general], message="ودني البوليفارد", has_attachments=False)
    assert (selected, reasons) == (["general"], ["general:fallback"])  # the rules alone never pick it
    assert router_candidates([way, general], has_indexed=False, message="ودني البوليفارد") == [way]
    assert router_candidates([way, general], has_indexed=False, message="ما عاصمة المملكة؟") == []
    # «طريق» inside a sentence about success still makes a candidate: the router model decides (I15).
    assert router_candidates([way, general], has_indexed=False, message="ما الطريق إلى النجاح؟") == [way]
