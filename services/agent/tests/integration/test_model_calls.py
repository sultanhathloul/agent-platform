"""NeedsMore is resolved once (README §7 step 9, §19.3 step 3; ADR-0056): the compress
pass, or one tier up when the capability asks to escalate and the cap and the
per-turn limit allow it. What still does not fit is context_overflow. No model is
called here."""

import dataclasses

import pytest

from app.context.assembler import ContextAssembler
from app.context.compress import truncate
from app.context.config import BlockBudget
from app.context.package import AssemblyRequest
from app.errors import TurnError
from app.graph.calls import prepare_call
from app.graph.deps import TurnContext
from app.identity import Principal

pytestmark = pytest.mark.integration


def _ctx(services, principal: Principal | None = None) -> TurnContext:
    return TurnContext(
        services=services, principal=principal or Principal(user_id="u-calls"), request_id="r", turn_id="t"
    )


def _capability(services, on_overflow: str):
    general = services.registry.get("general")
    model = general.manifest.model.model_copy(update={"on_overflow": on_overflow})
    return dataclasses.replace(general, manifest=general.manifest.model_copy(update={"model": model}))


def _text(services, tokens: int) -> str:
    """A text of about `tokens` tokens: a repeated sentence does not count additively."""
    return truncate("الرياض عاصمة المملكة. " * tokens, tokens, services.assembler.counter)


async def test_compress_is_the_second_pass(services):
    request = AssemblyRequest(
        node="answer",
        needs=["system", "tool_results", "user_message"],
        system_prompt="answer.system",
        locale="ar",
        state={"user_message": "سؤال"},
        tool_results=[{"tool": "lookup", "content": _text(services, 1900)}],
    )
    prepared = await prepare_call(
        _ctx(services),
        request,
        role="reasoning",
        step="answer",
        capabilities=[_capability(services, "compress")],
        max_input=1500,
    )
    assert prepared.package.compressed is True
    assert prepared.package.total_tokens() <= 1500
    assert (prepared.decision.final, prepared.decision.escalations) == ("standard", [])


def _roomy(services):
    """Findings may be large, so only the tier budget bounds the package."""
    config = services.assembler.config
    heavy = services.models_config.tiers["heavy"].window
    roomy = config.model_copy(update={"blocks": {**config.blocks, "findings": BlockBudget(max_tokens=heavy)}})
    return dataclasses.replace(
        services, assembler=ContextAssembler(roomy, services.prompts, services.assembler.counter)
    )


def _over_standard(services) -> AssemblyRequest:
    standard = services.models_config.tiers["standard"]
    headroom = services.assembler.config.reserved_loop_headroom_tokens
    tokens = standard.window - standard.max_output - headroom + 2000  # over standard, within heavy
    return AssemblyRequest(
        node="answer",
        needs=["system", "findings", "user_message"],
        system_prompt="answer.system",
        locale="ar",
        state={"user_message": "سؤال"},
        findings=[{"capability": "general", "findings": _text(services, tokens)}],
    )


async def test_escalate_moves_one_tier_up_and_assembles_again(services):
    roomy = _roomy(services)
    ctx = _ctx(roomy)
    prepared = await prepare_call(
        ctx,
        _over_standard(roomy),
        role="reasoning",
        step="answer",
        capabilities=[_capability(roomy, "escalate")],
    )
    assert prepared.decision.final == "heavy" and prepared.package.tier == "heavy"
    assert prepared.decision.escalations == ["context_overflow:heavy"]
    assert prepared.package.compressed is False and ctx.escalations == 1


async def test_an_escalation_the_cap_or_the_turn_limit_refuses_ends_as_context_overflow(services):
    roomy = _roomy(services)
    capped = _ctx(roomy, Principal(user_id="v", max_tier="standard"))
    spent = _ctx(roomy)
    spent.escalations = roomy.models_config.escalation.max_escalations_per_turn
    for ctx in (capped, spent):
        with pytest.raises(TurnError) as info:
            await prepare_call(
                ctx,
                _over_standard(roomy),
                role="reasoning",
                step="answer",
                capabilities=[_capability(roomy, "escalate")],
            )
        assert info.value.code == "context_overflow"
