import pytest
from pydantic import ValidationError

from app.guardrails.engine import GuardEngine
from app.models.config import ModelsConfig
from app.models.routing import CapabilityModelSpec, TierResolver


def test_repo_models_config_pins_helper_roles_to_fast(models_config):
    for role in ("router", "summarizer", "judge", "extractor"):
        assert models_config.roles[role] == "fast"
    assert models_config.order == ["fast", "standard", "heavy"]
    assert models_config.highest("fast", "heavy", "standard") == "heavy"
    assert models_config.lowest("heavy", "standard") == "standard"


def test_unknown_tier_reference_is_rejected(models_config):
    raw = models_config.model_dump()
    raw["roles"]["router"] = "turbo"
    with pytest.raises(ValidationError, match="roles.router"):
        ModelsConfig.model_validate(raw)


async def test_role_default_without_capabilities(models_config):
    decision = await TierResolver(models_config, GuardEngine()).resolve("reflection")
    assert (decision.floor, decision.final, decision.capped) == ("standard", "standard", False)
    assert "role default" in decision.reason


async def test_capability_floor_replaces_provisional_reasoning_tier(models_config):
    # A synthetic capability whose floor sits below the reasoning default;
    # `general` itself moved to standard in ADR-0024.
    on_fast = CapabilityModelSpec(slug="cap_on_fast", min_tier="fast")
    decision = await TierResolver(models_config, GuardEngine()).resolve(
        "reasoning", step="answer", capabilities=[on_fast]
    )
    assert decision.requested == "standard"
    assert decision.final == "fast"


async def test_highest_floor_and_step_override_win(models_config):
    sql = CapabilityModelSpec(slug="sql", min_tier="standard", steps={"fix_error": "fast"})
    kb = CapabilityModelSpec(slug="kb", min_tier="standard")
    resolver = TierResolver(models_config, GuardEngine())
    assert (await resolver.resolve("reasoning", step="fix_error", capabilities=[sql])).final == "fast"
    assert (await resolver.resolve("reasoning", step="fix_error", capabilities=[sql, kb])).final == "standard"


async def test_cap_bounds_the_final_tier(models_config):
    raw = models_config.model_dump()
    raw["caps"]["default"] = "standard"
    capped = ModelsConfig.model_validate(raw)
    deep = CapabilityModelSpec(slug="deep", min_tier="heavy")
    decision = await TierResolver(capped, GuardEngine()).resolve("reasoning", capabilities=[deep])
    assert (decision.floor, decision.cap, decision.final, decision.capped) == (
        "heavy",
        "standard",
        "standard",
        True,
    )
