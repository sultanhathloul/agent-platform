from app.graph.nodes.validate import MAX_ANSWER_CHARS, validation_issues
from app.text.script import contains_cjk

SOURCES = [{"id": "s1"}]


def test_arabic_answer_is_fine_without_cjk():
    assert validation_issues("عاصمة المملكة العربية السعودية هي الرياض.", [], [], locale="ar") == []


def test_arabic_answer_with_chinese_fails():
    assert validation_issues("العاصمة هي الرياض 北京 وهي مدينة كبيرة.", [], [], locale="ar") == [
        "cjk_in_arabic_answer"
    ]


def test_arabic_answer_with_kana_or_hangul_fails():
    assert validation_issues("الجواب هو ですね", [], [], locale="ar") == ["cjk_in_arabic_answer"]
    assert validation_issues("الجواب هو 안녕하세요", [], [], locale="ar") == ["cjk_in_arabic_answer"]


def test_english_locale_keeps_cjk():
    # Only the Arabic locale is script-checked; an English turn may quote CJK.
    assert validation_issues("The Chinese word for Beijing is 北京.", [], [], locale="en") == []


def test_latin_inside_an_arabic_answer_is_allowed():
    assert validation_issues("الرياض (Riyadh) هي العاصمة.", [], [], locale="ar") == []


def test_empty_answer_is_reported_once():
    assert validation_issues("   ", [], [], locale="ar") == ["empty_answer"]


def test_long_arabic_answer_with_cjk_reports_both():
    answer = "ا" * (MAX_ANSWER_CHARS + 1) + "北"
    assert validation_issues(answer, [], [], locale="ar") == ["answer_too_long", "cjk_in_arabic_answer"]


def test_citation_must_come_from_a_source():
    assert validation_issues("نص", [{"id": "s2"}], SOURCES, locale="ar") == ["citation_not_in_sources"]
    assert validation_issues("نص", [{"id": "s1"}], SOURCES, locale="ar") == []


def test_contains_cjk_ignores_arabic_and_latin():
    assert not contains_cjk("مرحبا Riyadh 123")
    assert contains_cjk("中")
