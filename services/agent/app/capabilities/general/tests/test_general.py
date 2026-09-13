from pathlib import Path

from app.capabilities.contract import CapabilityInput, CapabilityOutput, RuntimeContext
from app.capabilities.general.graph import build
from app.capabilities.registry import load_manifest
from app.context.package import ContextPackage
from app.guardrails.engine import GuardEngine
from app.identity import Principal
from app.models.routing import TierResolver

MANIFEST = Path(__file__).resolve().parents[1] / "manifest.yaml"


class _Unused:
    async def call(self, *args, **kwargs):
        raise AssertionError("general makes no model call")

    async def execute(self, *args, **kwargs):
        raise AssertionError("general uses no tools")


def test_manifest_is_valid(models_config):
    manifest = load_manifest(MANIFEST, models_config)
    assert manifest.permission == "cap:general"
    assert manifest.model.min_tier == "standard"  # ADR-0024
    assert [rule.when for rule in manifest.routing_hints.rules] == ["fallback"]
    assert manifest.name.ar and manifest.name.en


async def test_subgraph_returns_empty_finding_and_keeps_its_state(models_config):
    graph = build()
    principal = Principal(user_id="u1", permissions=("cap:general",))
    package = ContextPackage(node="capability.general", tier="standard", locale="ar", budget_limit=1000)
    cap_input = CapabilityInput(
        message="ما عاصمة المملكة؟",
        context=package,
        principal=principal,
        cap_state={"seen": 1},
        locale="ar",
    )
    runtime = RuntimeContext(
        slug="general",
        principal=principal,
        cap_state={"seen": 1},
        models=_Unused(),
        broker=_Unused(),
        resolver=TierResolver(models_config, GuardEngine()),
        stream_writer=lambda type_, data: None,
    )
    result = await graph.ainvoke({"input": cap_input}, context=runtime)
    output = result["output"]
    assert isinstance(output, CapabilityOutput)
    assert output.findings == ""
    assert output.sources == []
    assert output.cap_state == {"seen": 1}
