from pathlib import Path

from app.capabilities.contract import CapabilityInput, CapabilityOutput
from app.capabilities.general.graph import build
from app.capabilities.registry import load_manifest
from app.context.package import ContextPackage
from app.identity import Principal

MANIFEST = Path(__file__).resolve().parents[1] / "manifest.yaml"


def test_manifest_is_valid(models_config):
    manifest = load_manifest(MANIFEST, models_config)
    assert manifest.permission == "cap:general"
    assert manifest.model.min_tier == "standard"  # ADR-0024
    assert [rule.when for rule in manifest.routing_hints.rules] == ["fallback"]
    assert manifest.name.ar and manifest.name.en


async def test_subgraph_returns_empty_finding_and_keeps_its_state():
    graph = build()
    package = ContextPackage(node="general", tier="fast", locale="ar", budget_limit=1000)
    cap_input = CapabilityInput(
        message="ما عاصمة المملكة؟",
        context=package,
        principal=Principal(user_id="u1", permissions=("cap:general",)),
        cap_state={"seen": 1},
        locale="ar",
    )
    result = await graph.ainvoke({"input": cap_input})
    output = result["output"]
    assert isinstance(output, CapabilityOutput)
    assert output.findings == ""
    assert output.sources == []
    assert output.cap_state == {"seen": 1}
