"""Phase 06 contracts (G2), as types with tests: the router output with `skill`, the Context
Assembly `skill` block, the chat request field `skill`, the `skill` event, the history field."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.api.events import EVENT_ADAPTER, StateDigest, build_event
from app.api.schemas import HistoryMessage, SkillList, TurnRequest
from app.capabilities.contract import ContextSpec
from app.context.assembler import ContextAssembler, load_context_config
from app.context.budget import Budget, NeedsMore
from app.context.config import BUDGETED_BLOCKS
from app.context.package import AssemblyRequest, SkillBlockRequest
from app.graph.router import parse_decision
from app.identity import Principal
from app.skills.registry import load_skill
from app.skills.setup import block_request

INSTRUCTIONS = "رتّب الإجابة في نموذج: العنوان، ثم البنود، ثم الخاتمة."
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "skills"


def _budget(limit: int) -> Budget:
    return Budget(tier="fast", window=limit, reserved_output=0, reserved_headroom=0)


class CharCounter:
    def count(self, text: str) -> int:
        return len(text)


# ------------------------------------------------------------------ router output


def test_the_router_returns_at_most_one_skill_among_the_candidates():
    text = '{"capabilities": ["general"], "skill": "official_x", "complexity": "low", "risk": "low"}'
    decision = parse_decision(text, ["general"], ["official_x", "official_y"])
    assert decision.capabilities == ["general"] and decision.skill == "official_x" and decision.dropped_skill is None


def test_a_skill_that_is_not_a_candidate_is_dropped_and_reported():
    decision = parse_decision('{"capabilities": ["general"], "skill": "official_z"}', ["general"], ["official_x"])
    assert decision.skill is None and decision.dropped_skill == "official_z"


@pytest.mark.parametrize("value", ["null", '["official_x"]', "3", '""', "true"])
def test_any_other_skill_value_is_no_skill(value):
    decision = parse_decision(f'{{"capabilities": ["general"], "skill": {value}}}', ["general"], ["official_x"])
    assert decision.skill is None and decision.dropped_skill is None


def test_without_candidates_no_skill_is_ever_chosen():
    decision = parse_decision('{"capabilities": ["general"], "skill": "official_x"}', ["general"])
    assert decision.skill is None and decision.dropped_skill == "official_x"


# ------------------------------------------------------------------ the skill block


@pytest.fixture
def skill_prompts(prompts):
    return prompts


def _request(state, *, skill=None, needs=("system", "skill", "window", "user_message")):
    return AssemblyRequest(
        node="answer", needs=list(needs), system_prompt="answer.system", locale="ar", state=state, skill=skill
    )


SKILL = SkillBlockRequest(slug="demo_form", version="1.3.0", instructions=INSTRUCTIONS)


async def test_the_skill_block_is_system_role_right_after_the_system_instructions(config_dir, skill_prompts):
    assembler = ContextAssembler(load_context_config(config_dir), skill_prompts, CharCounter())
    state = {"turn": 2, "user_message": "اكتب نموذجًا", "messages": [{"role": "user", "content": "س"},
                                                                       {"role": "assistant", "content": "ج"}]}
    package = await assembler.assemble(_request(state, skill=SKILL), _budget(100_000))
    assert [b.name for b in package.blocks][:2] == ["system", "skill"]
    block = package.blocks[1]
    assert block.role == "system" and block.meta["skill"] == "demo_form@1.3.0"
    assert block.content.startswith('<skill id="demo_form" version="1.3.0">') and block.content.endswith("</skill>")
    assert INSTRUCTIONS in block.content
    assert package.prompt_refs[package.prompt_refs.index("demo_form@1.3.0") + 1].startswith("context.skill@")
    assert any(ref.startswith("context.skill@") for ref in package.prompt_refs)
    messages = package.to_messages()
    assert messages[0]["role"] == "system" and "<skill id=" in messages[0]["content"]
    assert all("<skill" not in m["content"] for m in messages[1:])


async def test_only_skill_md_and_the_files_it_names_reach_the_context(config_dir, skill_prompts):
    """The licence, an unreferenced Markdown file and the front-matter never reach the model (ADR-0318)."""
    skill = load_skill(FIXTURES / "fixture_form")
    assert "LICENSE.txt" in skill.skipped and "reference/unused.md" in skill.skipped
    assembler = ContextAssembler(load_context_config(config_dir), skill_prompts, CharCounter())
    package = await assembler.assemble(
        _request({"turn": 1, "user_message": "اكتب نموذج الاختبار"}, skill=block_request(skill)), _budget(100_000)
    )
    sent = "\n".join(m["content"] for m in package.to_messages())
    assert "FIXTURE-SKILL-INSTRUCTIONS fixture_form" in sent and "FIXTURE-SKILL-REFERENCE" in sent
    for absent in ("FIXTURE-SKILL-LICENSE", "FIXTURE-SKILL-UNREFERENCED", "keyword_candidate", "sovereign"):
        assert absent not in sent


async def test_no_skill_no_block(config_dir, skill_prompts):
    assembler = ContextAssembler(load_context_config(config_dir), skill_prompts, CharCounter())
    package = await assembler.assemble(_request({"turn": 1, "user_message": "س"}), _budget(100_000))
    assert "skill" not in [b.name for b in package.blocks]
    # Requested by no node but the answer: without `skill` in needs, a skill request renders nothing.
    package = await assembler.assemble(
        _request({"turn": 1, "user_message": "س"}, skill=SKILL, needs=("system", "user_message")),
        _budget(100_000),
    )
    assert "skill" not in [b.name for b in package.blocks]


async def test_the_budget_guard_never_drops_the_skill_block(config_dir, skill_prompts):
    assembler = ContextAssembler(load_context_config(config_dir), skill_prompts, CharCounter())
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": "ر" * 200} for i in range(6)]
    state = {"turn": 4, "user_message": "اكتب", "messages": history}
    full = await assembler.assemble(_request(state, skill=SKILL), _budget(100_000))
    system_and_skill = sum(b.tokens for b in full.blocks if b.name in ("system", "skill", "user_message"))
    tight = await assembler.assemble(_request(state, skill=SKILL), _budget(system_and_skill + 10))
    assert not isinstance(tight, NeedsMore)
    assert [b.name for b in tight.blocks] == ["system", "skill", "user_message"]
    assert all(d.block != "skill" for d in tight.drops)
    # Below what the protected blocks and the skill need, the package does not fit: NeedsMore, never a cut skill.
    short = await assembler.assemble(_request(state, skill=SKILL), _budget(system_and_skill - 5))
    assert isinstance(short, NeedsMore)
    assert any(b.name == "skill" for b in short.package.blocks)


def test_the_skill_block_has_a_budget_and_is_never_droppable(config_dir):
    config = load_context_config(config_dir)
    assert "skill" in BUDGETED_BLOCKS and config.blocks["skill"].max_tokens == 3000
    assert "skill" not in config.drop_order
    assert config.pipeline.index("skill_instructions") == config.pipeline.index("system_instructions") + 1


def test_a_capability_may_not_request_the_skill_block():
    with pytest.raises(ValidationError, match="only the answer node"):
        ContextSpec(needs=["window", "skill"])


# ------------------------------------------------------------------ request, event, history


def _turn(**extra):
    return {
        "conversation_id": "0b2f3c4d-5e6f-4a1b-8c9d-0e1f2a3b4c5d",
        "principal": Principal(user_id="u").model_dump(),
        "message": "اكتب مذكرة",
        **extra,
    }


def test_the_chat_request_carries_an_optional_skill_slug():
    assert TurnRequest.model_validate(_turn()).skill is None
    assert TurnRequest.model_validate(_turn(skill="official_x")).skill == "official_x"
    assert TurnRequest.model_validate(_turn(skill="internal-x")).skill == "internal-x"  # the open format's names
    for bad in ("Official", "skill:official_x", "../x", "x" * 65, "", "-x", "x y"):
        with pytest.raises(ValidationError):
            TurnRequest.model_validate(_turn(skill=bad))


def test_the_skill_event_carries_slug_and_labels_only():
    event = build_event("skill", {"slug": "official_x", "label": {"ar": "مذكرة", "en": "Memo"}},
                        request_id="r", turn_id="t", seq=3)
    assert EVENT_ADAPTER.validate_python(event.model_dump()).type == "skill"
    with pytest.raises(ValidationError):
        build_event("skill", {"slug": "official_x", "label": {"ar": "م", "en": "M"}, "instructions": "..."},
                    request_id="r", turn_id="t", seq=3)


def test_the_state_digest_names_the_applied_skill_by_ref():
    assert StateDigest(turn=1, capabilities=["general"]).skill is None
    assert StateDigest(turn=1, capabilities=["general"], skill="official_x@1.0.0").skill == "official_x@1.0.0"


def test_the_history_reply_carries_an_optional_skill():
    message = HistoryMessage(role="assistant", text="ج", turn_id="t")
    assert message.skill is None and "skill" in HistoryMessage.model_fields
    shown = HistoryMessage(role="assistant", text="ج", skill={"slug": "official_x", "label": {"ar": "م", "en": "M"}})
    assert shown.skill.slug == "official_x"


def test_the_skills_list_shape():
    listed = SkillList.model_validate(
        {"skills": [{"slug": "official_x", "name": {"ar": "م", "en": "M"}, "description": {"ar": "و", "en": "D"}}]}
    )
    assert listed.skills[0].slug == "official_x"
