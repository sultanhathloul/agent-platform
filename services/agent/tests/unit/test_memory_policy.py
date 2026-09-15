"""Memory policy (README §8, ADR-0058, ADR-0064): what the extractor's answer becomes,
when a turn writes nothing, and what the writer reads from the history."""

import json

import pytest
from langgraph.graph.message import add_messages

from app.graph.nodes.memory import _exchange
from app.memory.config import MemoryConfig
from app.memory.policy import grounding, guard_flagged, parse_candidates
from app.text.arabic import light_stems
from app.text.script import ScriptPolicy

CONFIG = MemoryConfig(
    enabled_by_default=True,
    kinds=["preference", "fact_about_user", "project_context", "decision"],
    min_confidence=0.6,
    max_items_per_turn=2,
    dedupe_similarity=0.92,
    min_similarity=0.5,
    max_text_chars=40,
    min_grounding=0.5,
    grounding_ignore=["المستخدم", "user", "the", "and"],
)
ARABIC = ScriptPolicy(letters=["ARABIC", "LATIN"])


def _output(*items: dict) -> str:
    return json.dumps(list(items), ensure_ascii=False)


def test_a_json_array_becomes_candidates_even_inside_prose_or_a_code_fence():
    output = (
        "النتيجة:\n```json\n"
        '[{"kind": "preference", "text": "يفضّل المستخدم   الإجابات المختصرة.", "confidence": 0.9}]\n```'
    )
    [candidate] = parse_candidates(output, CONFIG, source="أفضّل أن تكون الإجابات مختصرة.")
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
    assert parse_candidates(output, CONFIG, source="نص") == []


def test_kinds_confidence_duplicates_length_and_count_are_enforced():
    source = "قررت اعتماد الخطة الثانية، فأنا محلل بيانات في وزارة التعليم منذ سنوات، وهذا عنصر ثالث مقبول."
    output = _output(
        {"kind": "opinion", "text": "قرر اعتماد الخطة الثانية", "confidence": 0.99},
        {"kind": "decision", "text": "اعتماد الخطة الثانية", "confidence": 0.3},
        {"kind": "decision", "text": "قرر المستخدم اعتماد الخطة الثانية", "confidence": 0.8},
        {"kind": "decision", "text": "قرر المستخدم اعتماد الخطة الثانية", "confidence": 0.8},
        {
            "kind": "fact_about_user",
            "text": "المستخدم محلل بيانات في وزارة التعليم منذ سنوات",
            "confidence": 0.7,
        },
        {"kind": "project_context", "text": "عنصر ثالث مقبول", "confidence": 0.9},
    )
    kept = parse_candidates(output, CONFIG, source=source)
    assert [c.kind for c in kept] == ["decision", "fact_about_user"]
    assert len(kept[1].text) == CONFIG.max_text_chars


def test_only_candidates_grounded_in_the_users_message_in_the_locales_scripts_are_kept():
    """The live `fast` extractor's answers to the phase 02 probe (ADR-0064)."""
    smoke = "أعمل محللًا للبيانات في وزارة التعليم، وأفضّل أن تكون الإجابات مختصرة. ما عاصمة الأردن؟"
    kept = parse_candidates(
        _output(
            {
                "kind": "fact_about_user",
                "text": "المستخدم يعمل محللًا للبيانات في وزارة التعليم.",
                "confidence": 0.9,
            },
            {"kind": "preference", "text": "المستخدم يفضّل أن تكون الإجابات مختصرة.", "confidence": 0.9},
        ),
        CONFIG.model_copy(update={"max_text_chars": 500}),
        source=smoke,
        scripts=ARABIC,
    )
    assert [c.kind for c in kept] == ["fact_about_user", "preference"]

    inferred = _output(
        {
            "kind": "fact_about_user",
            "text": "المستخدم يرغب في الحصول على معلومة تاريخية حول عاصمة بلد معين.",
            "confidence": 0.95,
        }
    )
    assert parse_candidates(inferred, CONFIG, source="ما عاصمة البرتغال؟", scripts=ARABIC) == []
    # Grounded in the question, but about the topic rather than the user.
    restated = _output({"kind": "fact_about_user", "text": "عاصمة البرتغال هي لشبونة.", "confidence": 0.9})
    config = CONFIG.model_copy(update={"subject_words": ["المستخدم", "user"]})
    assert parse_candidates(restated, CONFIG, source="ما عاصمة البرتغال؟", scripts=ARABIC) != []
    assert parse_candidates(restated, config, source="ما عاصمة البرتغال؟", scripts=ARABIC) == []

    project = "نعمل على مشروع لتقليل زمن انتظار المراجعين في المستشفيات الحكومية."
    drifted = _output(
        {"kind": "project_context", "text": "ي.work على مشروع لتقليل زمن الانتظار", "confidence": 0.9}
    )
    assert parse_candidates(drifted, CONFIG, source=project, scripts=ARABIC) == []


def test_grounding_counts_content_words_after_normalization_and_light_stemming():
    source = "أفضّل أن تكون الإجابات مختصرة"
    # Without the ignore list, the extractor's own word "المستخدم" counts against the candidate.
    assert grounding("المستخدم يفضّل الإجابات المختصرة", source) == pytest.approx(2 / 4)
    assert grounding("المستخدم يفضّل الإجابات المختصرة", source, ["المستخدم"]) == pytest.approx(2 / 3)
    assert grounding(
        "The user wants plain language", "Write in plain language", ["the", "user"]
    ) == pytest.approx(2 / 3)
    assert grounding("في من", "في من") == 0.0  # no content word
    assert {"مملكه", "المملكه"} <= light_stems("بالمملكة")
    assert light_stems("ولد") == {"ولد"}  # stripping would leave fewer than three letters


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
