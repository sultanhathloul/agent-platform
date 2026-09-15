"""When the rolling summary folds messages (README §7 step 4, ADR-0057)."""

from app.context.summarizer import plan_summary


class CharCounter:
    def count(self, text: str) -> int:
        return len(text)


def _messages(n: int) -> list[dict]:
    """n messages of exactly 10 characters each."""
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"{i:02d}" + "م" * 8} for i in range(n)
    ]


def _plan(messages, *, cursor=0, summary=None, trigger=100):
    return plan_summary(
        messages,
        cursor=cursor,
        summary=summary,
        window_messages=6,
        trigger_tokens=trigger,
        counter=CharCounter(),
    )


def test_below_the_trigger_nothing_is_folded():
    plan = _plan(_messages(10))  # 100 tokens: not over 100
    assert (plan.history_tokens, plan.fold, plan.cursor_after) == (100, [], 0)


def test_past_the_trigger_the_messages_older_than_the_window_are_folded():
    messages = _messages(12)
    plan = _plan(messages)
    assert plan.history_tokens == 120
    assert plan.fold == messages[:6] and plan.cursor_after == 6


def test_retracted_messages_are_neither_counted_nor_folded():
    messages = _messages(12)
    messages[1] = {**messages[1], "retracted": True}
    plan = _plan(messages, trigger=105)
    assert plan.history_tokens == 110
    assert plan.fold == [m for i, m in enumerate(messages[:6]) if i != 1] and plan.cursor_after == 6


def test_the_summary_counts_toward_the_trigger():
    assert _plan(_messages(10), summary="م" * 5).history_tokens == 105


def test_once_a_summary_exists_each_turn_folds_what_left_the_window():
    messages = _messages(12)
    plan = _plan(messages, cursor=4, summary="ملخص", trigger=10_000)
    assert plan.fold == messages[4:6] and plan.cursor_after == 6


def test_nothing_is_folded_while_the_window_holds_every_unsummarized_message():
    plan = _plan(_messages(12), cursor=6, summary="ملخص", trigger=1)
    assert plan.fold == [] and plan.cursor_after == 6
