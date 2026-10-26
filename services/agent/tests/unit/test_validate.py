"""Answer validation (README §5.2): citations, length, and the writing scripts an
answer may use, from config/validation.yaml (ADR-0060)."""

import pytest
from pydantic import ValidationError

from app.graph.nodes.validate import MAX_ANSWER_CHARS, validation_issues
from app.text.script import (
    DISALLOWED_SCRIPT,
    MIXED_SCRIPT_TOKEN,
    ScriptPolicy,
    ValidationConfig,
    load_validation_config,
    script_issues,
    script_of,
)

SOURCES = [{"id": "s1"}]


@pytest.fixture(scope="module")
def arabic(config_dir) -> ScriptPolicy:
    policy = load_validation_config(config_dir).scripts_for("ar")
    assert policy is not None
    return policy


def test_arabic_answer_passes(arabic):
    assert validation_issues("عاصمة المملكة العربية السعودية هي الرياض.", [], [], scripts=arabic) == []


@pytest.mark.parametrize(
    "answer",
    [
        "الرياض (Riyadh) هي العاصمة.",
        "العملة الرسمية هي الريال السعودي (SAR).",
        "بلغ عدد السكان 32 مليونًا عام 2022م.",
        "النسبة ٤٥٪ تقريبًا، أي أقل من النصف.",
        "افتح الرابط https://www.my.gov.sa للمزيد.",
        "الاستعلام المنفذ: SELECT count(*) FROM sales;",
        "عاصمة البرتغال هي لشبونة (Lisbon)، وعملتها اليورو (€).",
        "مُحَمَّدٌ رَسُولُ اللَّهِ",
    ],
    ids=["latin-word", "latin-code", "digits", "arabic-digits", "url", "sql", "symbols", "diacritics"],
)
def test_latin_whole_tokens_digits_and_punctuation_pass(arabic, answer):
    assert script_issues(answer, arabic) == []


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        # The three samples measured on the standard tier in phase 01 (ADR-0033, ADR-0060).
        ("العملة الرسمية في المملكة العربية السعودية هي الريyal السعودي.", [MIXED_SCRIPT_TOKEN]),
        ("عاصمة البرتugal هي لشبونة.", [MIXED_SCRIPT_TOKEN]),
        ("عاصمة البرتغال هي لисboa.", [DISALLOWED_SCRIPT, MIXED_SCRIPT_TOKEN]),
        # A token entirely in another script fails anywhere.
        ("عاصمة روسيا هي Москва.", [DISALLOWED_SCRIPT]),
        ("عاصمة اليونان هي Αθήνα.", [DISALLOWED_SCRIPT]),
        ("العاصمة هي الرياض 北京 وهي مدينة كبيرة.", [DISALLOWED_SCRIPT]),
        ("الجواب هو ですね", [DISALLOWED_SCRIPT]),
        ("الجواب هو 안녕하세요", [DISALLOWED_SCRIPT]),
    ],
    ids=["riyal", "portugal", "lisboa", "cyrillic", "greek", "chinese", "kana", "hangul"],
)
def test_script_failures_in_arabic_answers(arabic, answer, expected):
    assert script_issues(answer, arabic) == expected
    assert validation_issues(answer, [], [], scripts=arabic) == expected


def test_a_mixed_token_passes_only_when_allowlisted(arabic):
    answer = "يمكنك استخدام (الـAPI) مباشرة."
    assert script_issues(answer, arabic) == [MIXED_SCRIPT_TOKEN]
    allowlisted = arabic.model_copy(update={"mixed_token_allowlist": ["الـAPI"]})
    assert script_issues(answer, allowlisted) == []  # compared after trimming the parentheses
    assert script_issues("يمكنك استخدام الـAPIs.", allowlisted) == [MIXED_SCRIPT_TOKEN]


def test_script_of_classifies_letters_and_leaves_the_rest_neutral():
    assert [script_of(c) for c in "عaжα中"] == ["ARABIC", "LATIN", "CYRILLIC", "GREEK", "CJK"]
    assert script_of("َ") == "ARABIC"  # fatha belongs to the Arabic script
    for neutral in ("٣", "7", "،", "؟", "%", "€", " ", "‌", "́"):
        assert script_of(neutral) is None, repr(neutral)


# Emoji sequences (phase 08, F1, ADR-0340): a variation selector (🏖️ and ⚙️ end in U+FE0F), a
# skin-tone modifier, a zero-width joiner, a keycap, a regional-indicator flag or a tag sequence
# carries no writing system, wherever it sits in a token.
EMOJI_ANSWERS = [
    "🏖️ فريق الموارد البشرية: تبدأ الإجازة الصيفية يوم الأحد.",
    "⚙️ فريق التطوير أنهى ترحيل الخادم هذا الأسبوع.",
    "⚙️ Engineering team: the migration finished on Sunday.",
    "🏖️ HR: summer leave starts on Sunday, see the FAQ.",
    "تهانينا للفريق 👍🏽 على الإنجاز، والمراجعة يوم 2️⃣ من الشهر 🇸🇦.",
    "Kudos 👩🏾‍💻 to the platform team and 👨‍👩‍👧 families day 🏴󠁧󠁢󠁳󠁣󠁴󠁿 #1️⃣",
    "فريق🏖️ التشغيل: شكرًا (Ops ⚙️) على الدعم.",
]


@pytest.mark.parametrize("answer", EMOJI_ANSWERS)
def test_emoji_selectors_modifiers_and_joiners_carry_no_script(arabic, answer):
    assert script_issues(answer, arabic) == []
    assert validation_issues(answer, [], [], scripts=arabic) == []


def test_emoji_components_are_neutral_and_a_real_disallowed_script_still_fails(arabic):
    for neutral in ("\ufe0f", "\ufe0e", "\U000e0100", "\U0001f3fd", "\u200d", "\u20e3", "\U0001f1f8", "\U000e0067"):
        assert script_of(neutral) is None, repr(neutral)
    assert script_issues("🏖️ فريق الموارد البشرية: Отпуск начинается в воскресенье.", arabic) == [DISALLOWED_SCRIPT]
    assert script_issues("⚙️ 北京 فريق التطوير", arabic) == [DISALLOWED_SCRIPT]
    assert script_issues("⚙️ الريyal", arabic) == [MIXED_SCRIPT_TOKEN]


def test_a_locale_without_a_policy_is_not_script_checked(config_dir):
    assert load_validation_config(config_dir).scripts_for("en") is None
    assert validation_issues("The Chinese word for Beijing is 北京.", [], [], scripts=None) == []


def test_retry_names_only_script_issues():
    with pytest.raises(ValidationError):
        ValidationConfig.model_validate(
            {"retry": {"issues": ["empty_answer"], "attempts": 1, "temperature": 0}}
        )


def test_empty_answer_is_reported_once(arabic):
    assert validation_issues("   ", [], [], scripts=arabic) == ["empty_answer"]


def test_long_arabic_answer_with_cjk_reports_both(arabic):
    answer = "ا" * (MAX_ANSWER_CHARS + 1) + " 北"
    assert validation_issues(answer, [], [], scripts=arabic) == ["answer_too_long", DISALLOWED_SCRIPT]


def test_citation_must_come_from_a_source(arabic):
    assert validation_issues("نص", [{"id": "s2"}], SOURCES, scripts=arabic) == ["citation_not_in_sources"]
    assert validation_issues("نص", [{"id": "s1"}], SOURCES, scripts=arabic) == []
