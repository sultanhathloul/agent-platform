"""Context escalation in the tier resolver (README §19.3 step 3, ADR-0056)."""

from app.guardrails.engine import GuardEngine
from app.identity import Principal
from app.models.routing import CapabilityModelSpec, TierResolver

USER = Principal(user_id="u-1")


async def test_escalate_moves_one_tier_up_within_the_cap(models_config):
    resolver = TierResolver(models_config, GuardEngine())
    decision = await resolver.resolve("reasoning", principal=USER)
    assert decision.final == "standard"

    up = await resolver.escalate(decision, principal=USER, reason="context_overflow")
    assert (up.final, up.escalations, up.capped) == ("heavy", ["context_overflow:heavy"], False)
    assert up.reason.endswith("; escalated for context_overflow")
    assert (await resolver.escalate(up, principal=USER, reason="context_overflow")).final == "heavy"

    viewer = Principal(user_id="v", max_tier="standard")
    capped = await resolver.escalate(decision, principal=viewer, reason="context_overflow")
    assert (capped.final, capped.capped, capped.cap) == ("standard", True, "standard")
    assert "capped by principal.max_tier" in capped.reason


def test_the_manifest_decides_the_overflow_action_before_the_config_default(models_config):
    resolver = TierResolver(models_config, GuardEngine())
    silent = CapabilityModelSpec(slug="silent", min_tier="standard")
    compress = CapabilityModelSpec(slug="small", min_tier="standard", on_overflow="compress")
    escalate = CapabilityModelSpec(slug="deep", min_tier="standard", on_overflow="escalate")
    assert resolver.overflow_action([]) == models_config.escalation.context_overflow_default
    assert resolver.overflow_action([silent]) == models_config.escalation.context_overflow_default
    assert resolver.overflow_action([compress]) == "compress"
    assert resolver.overflow_action([compress, escalate]) == "escalate"
