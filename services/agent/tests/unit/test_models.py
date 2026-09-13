import pytest
from pydantic import ValidationError

from app.guardrails.engine import GuardEngine
from app.identity import Principal
from app.models.config import ModelsConfig
from app.models.routing import CapabilityModelSpec, TierResolver

USER = Principal(user_id="u-1")


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


def test_tiers_are_the_known_names_and_caps_need_a_default(models_config):
    raw = models_config.model_dump()
    raw["tiers"]["turbo"] = raw["tiers"]["heavy"]
    raw["order"].append("turbo")
    with pytest.raises(ValidationError):
        ModelsConfig.model_validate(raw)

    raw = models_config.model_dump()
    del raw["caps"]["default"]
    with pytest.raises(ValidationError, match="caps.default"):
        ModelsConfig.model_validate(raw)


async def test_role_default_without_capabilities(models_config):
    decision = await TierResolver(models_config, GuardEngine()).resolve("reflection", principal=USER)
    assert (decision.floor, decision.final, decision.capped) == ("standard", "standard", False)
    assert "role default" in decision.reason


async def test_capability_floor_replaces_provisional_reasoning_tier(models_config):
    # A synthetic capability whose floor sits below the reasoning default;
    # `general` itself moved to standard in ADR-0024.
    on_fast = CapabilityModelSpec(slug="cap_on_fast", min_tier="fast")
    decision = await TierResolver(models_config, GuardEngine()).resolve(
        "reasoning", step="answer", capabilities=[on_fast], principal=USER
    )
    assert decision.requested == "standard"
    assert decision.final == "fast"


async def test_highest_floor_and_step_override_win(models_config):
    sql = CapabilityModelSpec(slug="sql", min_tier="standard", steps={"fix_error": "fast"})
    kb = CapabilityModelSpec(slug="kb", min_tier="standard")
    resolver = TierResolver(models_config, GuardEngine())
    fix_sql = await resolver.resolve("reasoning", step="fix_error", capabilities=[sql], principal=USER)
    assert fix_sql.final == "fast"
    fix_both = await resolver.resolve("reasoning", step="fix_error", capabilities=[sql, kb], principal=USER)
    assert fix_both.final == "standard"


def test_repo_models_config_makes_helper_roles_deterministic(models_config):
    # ADR-0032: helper roles decode greedily, the answer keeps a little sampling.
    for role in ("router", "summarizer", "judge", "extractor"):
        assert models_config.temperature[role] == 0.0
    assert models_config.temperature["reasoning"] == 0.2
    assert models_config.temperature["reflection"] == 0.2


def test_every_role_needs_a_temperature(models_config):
    raw = models_config.model_dump()
    del raw["temperature"]["judge"]
    with pytest.raises(ValidationError, match="temperature must name every role"):
        ModelsConfig.model_validate(raw)

    raw = models_config.model_dump()
    raw["temperature"]["spare"] = 0.0
    with pytest.raises(ValidationError, match="temperature must name every role"):
        ModelsConfig.model_validate(raw)


def test_temperature_out_of_range_is_rejected(models_config):
    raw = models_config.model_dump()
    raw["temperature"]["reasoning"] = 2.5
    with pytest.raises(ValidationError, match="temperature.reasoning"):
        ModelsConfig.model_validate(raw)


def _with_default_cap(models_config, tier: str) -> ModelsConfig:
    raw = models_config.model_dump()
    raw["caps"]["default"] = tier
    return ModelsConfig.model_validate(raw)


async def test_cap_bounds_the_final_tier(models_config):
    deep = CapabilityModelSpec(slug="deep", min_tier="heavy")
    decision = await TierResolver(_with_default_cap(models_config, "standard"), GuardEngine()).resolve(
        "reasoning", capabilities=[deep], principal=USER
    )
    assert (decision.floor, decision.cap, decision.final, decision.capped) == (
        "heavy",
        "standard",
        "standard",
        True,
    )


async def test_principal_max_tier_caps_the_final_tier(models_config):
    # The gateway sends the strictest role ceiling as principal.max_tier (ADR-0046).
    deep = CapabilityModelSpec(slug="deep", min_tier="heavy")
    resolver = TierResolver(models_config, GuardEngine())

    viewer = Principal(user_id="v", max_tier="standard")
    capped = await resolver.resolve("reasoning", capabilities=[deep], principal=viewer)
    assert (capped.cap, capped.final, capped.capped) == ("standard", "standard", True)
    assert "principal.max_tier" in capped.reason

    uncapped = await resolver.resolve("reasoning", capabilities=[deep], principal=USER)
    assert (uncapped.cap, uncapped.final, uncapped.capped) == ("heavy", "heavy", False)


async def test_role_cap_above_the_default_is_bounded_by_the_default(models_config):
    # Caps are ceilings (ADR-0049): role:x: heavy under default: standard yields standard.
    deep = CapabilityModelSpec(slug="deep", min_tier="heavy")
    resolver = TierResolver(_with_default_cap(models_config, "standard"), GuardEngine())
    decision = await resolver.resolve(
        "reasoning", capabilities=[deep], principal=Principal(user_id="x", max_tier="heavy")
    )
    assert (decision.cap, decision.final, decision.capped) == ("standard", "standard", True)
    assert "caps.default" in decision.reason


async def test_resolve_requires_a_principal(models_config):
    with pytest.raises(TypeError, match="principal"):
        await TierResolver(models_config, GuardEngine()).resolve("reasoning")


def test_unknown_max_tier_is_rejected_by_the_schema():
    with pytest.raises(ValidationError, match="max_tier"):
        Principal(user_id="x", max_tier="turbo")
    assert Principal.model_json_schema()["properties"]["max_tier"]["anyOf"][0]["enum"] == [
        "fast",
        "standard",
        "heavy",
    ]


def test_agent_principal_carries_no_roles():
    with pytest.raises(ValidationError):
        Principal.model_validate({"user_id": "x", "roles": ["viewer"]})
