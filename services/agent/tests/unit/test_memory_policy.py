"""Memory policy (README §8, ADR-0058): what the extractor's answer becomes, when a
turn writes nothing, and what the writer reads from the history."""

import json

import pytest
from langgraph.graph.message import add_messages

from app.graph.nodes.memory import _exchange
from app.memory.config import MemoryConfig
from app.memory.policy import guard_flagged, parse_candidates

CONFIG = MemoryConfig(
    enabled_by_default=True,
    kinds=["preference", "fact_about_user", "project_context", "decision"],
    min_confidence=0.6,
    max_items_per_turn=2,
    dedupe_similarity=0.92,
    min_similarity=0.5,
    max_text_chars=40,
)


def test_a_json_array_becomes_candidates_even_inside_prose_or_a_code_fence():
    output = (
        "النتيجة:\n```json\n"
        '[{"kind": "preference", "text": "يفضّل المستخدم   الإجابات المختصرة.", "confidence": 0.9}]\n```'
    )
    [candidate] = parse_candidates(output, CONFIG)
    assert (candidate.kind, candidate.text, candidate.confidence) == (
        "preference",
        "يفضّل المستخدم الإجابات المختصرة.",
        0.9,
    )


@pytest.mark.parametrize(
    "output",
    [
        "",
        "لا يوجد ما يستحق التذكر.",
        "[]",
        '[{"kind": "preference"}]',
        '{"kind": "preference", "text": "نص", "confidence": 1}',
        "[ليس JSON]",
    ],
    ids=["empty", "prose", "empty-array", "missing-fields", "object", "not-json"],
)
def test_malformed_or_empty_output_stores_nothing(output):
    assert parse_candidates(output, CONFIG) == []


def test_kinds_confidence_duplicates_length_and_count_are_enforced():
    items = [
        {"kind": "opinion", "text": "رأي عام", "confidence": 0.99},
        {"kind": "preference", "text": "ثقة منخفضة", "confidence": 0.3},
        {"kind": "decision", "text": "قرر المستخدم اعتماد الخطة الثانية", "confidence": 0.8},
        {"kind": "decision", "text": "قرر المستخدم اعتماد الخطة الثانية", "confidence": 0.8},
        {
            "kind": "fact_about_user",
            "text": "المستخدم محلل بيانات في وزارة التعليم منذ سنوات",
            "confidence": 0.7,
        },
        {"kind": "project_context", "text": "عنصر ثالث مقبول يتجاوز الحد", "confidence": 0.9},
    ]
    kept = parse_candidates(json.dumps(items, ensure_ascii=False), CONFIG)
    assert [c.kind for c in kept] == ["decision", "fact_about_user"]
    assert len(kept[1].text) == CONFIG.max_text_chars


def test_a_guard_action_other_than_allow_or_log_only_flags_the_turn():
    assert not guard_flagged([{"action": "allow"}, {"action": "log_only"}])
    for action in ("warn", "redact", "rewrite", "block", "require_approval"):
        assert guard_flagged([{"action": "allow"}, {"action": action}])


def test_the_writer_reads_the_post_guard_content_of_this_turn_only():
    history = add_messages(
        [],
        [
            {"role": "user", "content": "سؤال سابق"},
            {"role": "assistant", "content": "إجابة سابقة", "turn_id": "turn-0"},
            {"role": "user", "content": "ما رقم الجوال المسجل؟"},
            {
                "role": "assistant",
                "content": "الرقم المسجل [REDACTED:phone]",
                "turn_id": "turn-1",
                "shown_answer": "الرقم المسجل 0501234567",
            },
        ],
    )
    assert _exchange({"messages": history, "turn_id": "turn-1"}) == [
        {"role": "user", "content": "ما رقم الجوال المسجل؟"},
        {"role": "assistant", "content": "الرقم المسجل [REDACTED:phone]"},
    ]
    assert _exchange({"messages": history, "turn_id": "turn-2"}) is None  # this turn persisted nothing
