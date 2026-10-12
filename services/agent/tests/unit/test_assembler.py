import pytest
from langgraph.graph.message import add_messages

from app.context.assembler import ContextAssembler, load_context_config
from app.context.budget import Budget, NeedsMore
from app.context.package import AssemblyRequest
from app.context.tokens import TiktokenCounter

ALL_NEEDS = ["system", "state_digest", "window", "findings", "user_message"]


class CharCounter:
    """Deterministic counts (1 char = 1 token) so budget arithmetic is exact."""

    def count(self, text: str) -> int:
        return len(text)


def _request(state, *, needs=ALL_NEEDS, findings=()):
    return AssemblyRequest(
        node="answer",
        needs=list(needs),
        system_prompt="answer.system",
        locale="ar",
        state=state,
        findings=list(findings),
    )


def _budget(limit: int) -> Budget:
    return Budget(tier="fast", window=limit, reserved_output=0, reserved_headroom=0)


def _history(n: int) -> list[dict]:
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"رسالة رقم {i}"} for i in range(n)]


@pytest.fixture
def assembler(config_dir, prompts):
    return ContextAssembler(load_context_config(config_dir), prompts, CharCounter())


async def test_blocks_are_ordered_and_counted(assembler):
    state = {"turn": 2, "user_message": "ما عاصمة المملكة؟", "messages": _history(2), "selected_kbs": ["moe"]}
    package = await assembler.assemble(_request(state), _budget(100_000))
    assert [b.name for b in package.blocks] == ["system", "state_digest", "window", "window", "user_message"]
    assert all(b.tokens == len(b.content) for b in package.blocks)
    assert package.tokens_by_block()["window"] == len("رسالة رقم 0") + len("رسالة رقم 1")
    assert package.total_tokens() == sum(package.tokens_by_block().values())
    assert package.prompt_refs == ["answer.system@1.4.0", "context.state_digest@1.1.0"]

    messages = package.to_messages()
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[-1]["content"] == "ما عاصمة المملكة؟"
    assert "قواعد المعرفة المختارة: moe" in messages[0]["content"]


async def test_empty_state_digest_is_omitted(assembler):
    state = {"turn": 1, "user_message": "سؤال", "messages": []}
    package = await assembler.assemble(_request(state), _budget(100_000))
    assert [b.name for b in package.blocks] == ["system", "user_message"]
    assert package.prompt_refs == ["answer.system@1.4.0"]


async def test_needs_filter_keeps_system_and_user_message(assembler):
    state = {"turn": 1, "user_message": "سؤال", "messages": _history(4)}
    package = await assembler.assemble(_request(state, needs=[]), _budget(100_000))
    assert [b.name for b in package.blocks] == ["system", "user_message"]


async def test_window_keeps_only_the_last_n_messages(assembler):
    state = {"turn": 6, "user_message": "سؤال", "messages": _history(10)}
    package = await assembler.assemble(_request(state), _budget(100_000))
    window = [b.content for b in package.blocks if b.name == "window"]
    assert window == [f"رسالة رقم {i}" for i in range(4, 10)]


async def test_window_skips_a_retracted_exchange_as_a_pair(assembler):
    # ADR-0048, ADR-0076: a reply withdrawn after its turn never reaches a model again,
    # and neither does the question stored with it.
    plain = [
        {"role": "user", "content": "سؤال أول"},
        {"role": "assistant", "content": "إجابة مسحوبة", "retracted": True},
        {"role": "user", "content": "سؤال ثانٍ"},
        {"role": "assistant", "content": "إجابة سليمة"},
    ]
    # Checkpointed messages carry the flag in additional_kwargs; plain dicts at the top level.
    for messages in (add_messages([], plain), plain):
        state = {"turn": 3, "user_message": "سؤال", "messages": messages}
        package = await assembler.assemble(_request(state), _budget(100_000))
        window = [b.content for b in package.blocks if b.name == "window"]
        assert window == ["سؤال ثانٍ", "إجابة سليمة"]


async def test_findings_block_only_with_evidence(assembler):
    state = {"turn": 1, "user_message": "سؤال", "messages": []}
    empty = await assembler.assemble(
        _request(state, findings=[{"capability": "general", "findings": ""}]), _budget(10**5)
    )
    assert "findings" not in empty.tokens_by_block()
    full = await assembler.assemble(
        _request(state, findings=[{"capability": "kb", "findings": "نص الدليل"}]), _budget(10**5)
    )
    assert "نص الدليل" in full.blocks[[b.name for b in full.blocks].index("findings")].content


async def test_budget_guard_drops_oldest_window_messages_first(assembler):
    state = {"turn": 3, "user_message": "سؤال", "messages": _history(4)}
    unlimited = await assembler.assemble(_request(state), _budget(10**5))
    window = [b for b in unlimited.blocks if b.name == "window"]
    limit = unlimited.total_tokens() - window[0].tokens - window[1].tokens

    package = await assembler.assemble(_request(state), _budget(limit))
    assert not isinstance(package, NeedsMore)
    assert [d.block for d in package.drops] == ["window", "window"]
    assert [b.content for b in package.blocks if b.name == "window"] == ["رسالة رقم 2", "رسالة رقم 3"]
    assert package.total_tokens() <= limit


async def test_needs_more_when_protected_blocks_cannot_fit(assembler):
    state = {"turn": 1, "user_message": "سؤال طويل " * 20, "messages": _history(2)}
    result = await assembler.assemble(_request(state), _budget(50))
    assert isinstance(result, NeedsMore)
    assert result.deficit_tokens > 0
    names = [b.name for b in result.package.blocks]
    assert "system" in names and "user_message" in names and "window" not in names


def test_budget_limit_reserves_output_and_headroom(models_config):
    fast = models_config.tiers["fast"]
    budget = Budget.for_tier("fast", fast, headroom=1000)
    assert budget.limit == fast.window - fast.max_output - 1000
    assert Budget.for_tier("fast", fast, headroom=1000, max_input=6000).limit == 6000


def test_real_counter_handles_arabic():
    counter = TiktokenCounter()
    text = "المملكة العربية السعودية دولة عربية تقع في جنوب غرب آسيا."
    assert 0 < counter.count(text) < len(text)
    assert counter.count("") == 0
