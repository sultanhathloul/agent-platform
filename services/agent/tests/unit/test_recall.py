"""conversation_recall (report library PR, owner decision 5, ADR-0434): the conversation's whole stored
history searched, matching earlier messages returned verbatim, linear and capped."""

from app.artifacts.library import RecallConfig, Words
from app.context.recall import recall

TABLE = "\n".join(
    ["المدينة\tالسكان\tالمساحة", "الرياض\t7.6\t1973", "جدة\t3.7\t1686", "مكة\t2.4\t1200", "الدمام\t1.5\t800",
     "المدينة المنورة\t1.4\t589"]
)
CONFIG = RecallConfig(
    max_messages=2000, max_scan_chars=600000, max_results=3, message_chars=6000, max_chars=12000, skip_recent=2,
    table_words=["جدول", "الجدول", "table"], list_words=["قائمه"], early_words=["البدايه", "اول", "first"],
    stopwords=Words(ar=["كم", "في", "اللي", "ذكرت", "الجدول", "البدايه"], en=["the", "what"]),
)


def _history() -> list[dict]:
    messages = [{"role": "user", "content": "خذ هذا الجدول:\n" + TABLE}, {"role": "assistant", "content": "تم."}]
    messages += [{"role": "user", "content": "كم نسبة البطالة؟"}, {"role": "assistant", "content": "نسبة البطالة 6.5%."}]
    for i in range(20):
        messages += [{"role": "user", "content": f"سؤال عام رقم {i} عن الطقس"}, {"role": "assistant", "content": f"جواب {i}"}]
    return messages


def test_a_question_about_the_pasted_table_returns_it_verbatim():
    found = recall(_history(), "وش كان عدد سكان جدة في الجدول اللي أرسلته؟", CONFIG)
    assert found[0].text.endswith(TABLE) and found[0].role == "user" and found[0].exchange == 1


def test_a_figure_from_an_early_turn_is_found_by_its_words():
    found = recall(_history(), "كم كانت نسبة البطالة اللي ذكرتها في البداية؟", CONFIG)
    assert any("6.5%" in r.text for r in found)


def test_the_window_messages_are_skipped_and_retracted_exchanges_never_return():
    history = _history() + [{"role": "user", "content": "كلمة_سرية جديدة"}, {"role": "assistant", "content": "حسنًا"}]
    assert recall(history, "كلمة_سرية", CONFIG) == []  # the last exchange is in the window
    retracted = [{"role": "user", "content": "كلمة_مسحوبة"}, {"role": "assistant", "content": "x", "retracted": True}]
    assert recall(retracted + _history(), "كلمة_مسحوبة", CONFIG) == []


def test_results_and_sizes_are_capped():
    long = [{"role": "user", "content": "بطالة " * 5000}, {"role": "assistant", "content": "بطالة"}] * 10
    small = CONFIG.model_copy(update={"message_chars": 300, "max_chars": 700, "max_results": 5})
    found = recall(long + _history(), "بطالة", small)
    assert len(found) <= 5 and sum(len(r.text) for r in found) <= 700 and all(len(r.text) <= 300 for r in found)
    assert any(r.cut for r in found)


def test_the_scan_is_bounded_by_characters_newest_first():
    old = [{"role": "user", "content": "كلمة_قديمة"}, {"role": "assistant", "content": "تم"}]
    filler = [{"role": "user", "content": "حشو " * 200}, {"role": "assistant", "content": "تم"}] * 20
    tight = CONFIG.model_copy(update={"max_scan_chars": 5000})
    assert recall(old + filler, "كلمة_قديمة", tight) == []  # past the cap from the newest
    assert recall(old + filler, "كلمة_قديمة", CONFIG)


def test_nothing_matches_nothing_returns():
    assert recall(_history(), "الفلسفة اليونانية", CONFIG) == []
