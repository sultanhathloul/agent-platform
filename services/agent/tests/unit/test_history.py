"""The client's view of a conversation's history (ADR-0193): post-guard text only, a
retracted exchange left out as a pair, file ids and citations when they were recorded."""

from langchain_core.messages import AIMessage, HumanMessage

from app.api.history import first_question, history
from app.api.ownership import TITLE_MAX_CHARS, title_of


def test_only_the_post_guard_text_leaves_never_shown_answer_or_retract_reason():
    messages = [
        HumanMessage("رقم جوالي 0551234567", additional_kwargs={"attachment_ids": ["a-1"]}),
        AIMessage(
            "سجلت رقمك [محجوب]",
            additional_kwargs={
                "turn_id": "t1",
                "shown_answer": "سجلت رقمك 0551234567",
                "citations": [{"id": "a-1", "title": "لائحة.pdf", "source": "doc", "pages": [2]}],
            },
        ),
    ]
    shown = history(messages)
    assert shown == [
        {"role": "user", "text": "رقم جوالي 0551234567", "attachment_ids": ["a-1"]},
        {
            "role": "assistant",
            "text": "سجلت رقمك [محجوب]",
            "turn_id": "t1",
            "citations": [{"id": "a-1", "title": "لائحة.pdf", "source": "doc", "pages": [2]}],
        },
    ]
    assert "0551234567" not in str(shown[1])


def test_a_retracted_exchange_is_left_out_with_its_question():
    messages = [
        HumanMessage("السؤال الأول"),
        AIMessage(
            "جواب مسحوب", additional_kwargs={"turn_id": "t1", "retracted": True, "retract_reason": "audit"}
        ),
        HumanMessage("السؤال الثاني"),
        AIMessage("الجواب الثاني", additional_kwargs={"turn_id": "t2"}),
    ]
    assert [m["text"] for m in history(messages)] == ["السؤال الثاني", "الجواب الثاني"]
    assert first_question(messages) == "السؤال الثاني"
    assert "retract_reason" not in str(history(messages))


def test_messages_from_before_the_change_have_no_ids_or_citations():
    shown = history(
        [{"role": "user", "content": "مرحبا"}, {"role": "assistant", "content": "أهلا", "turn_id": "t"}]
    )
    assert shown == [
        {"role": "user", "text": "مرحبا", "attachment_ids": []},
        {"role": "assistant", "text": "أهلا", "turn_id": "t", "citations": []},
    ]
    assert first_question([]) is None


def test_a_title_is_the_first_question_on_one_line_and_bounded():
    assert title_of("  ما   عاصمة\nالمملكة؟ ") == "ما عاصمة المملكة؟"
    long = "كلمة " * 40
    assert len(title_of(long)) <= TITLE_MAX_CHARS and title_of(long).endswith("…")
