"""Number words folded to digits for citation matching (owner decision of 2026-10-27, ADR-0359): case
endings and gender forms, tens and units joined by «و», hundreds and thousands, percentages written as
words, and English number words; everything that is not a number word is left as it is."""

import pytest

from app.text.arabic import fold_number_words


@pytest.mark.parametrize(
    ("text", "folded"),
    [
        # Case endings: nominative -ون / -ان and accusative-genitive -ين fold alike.
        ("عشرون يوما", "20 يوما"),
        ("عشرين يوما", "20 يوما"),
        ("تسعون يوما", "90 يوما"),
        ("تسعين يوما", "90 يوما"),
        ("اثنان", "2"),
        ("اثنين", "2"),
        ("اثنتان", "2"),
        ("اثنتين", "2"),
        # Gender forms: ثلاث/ثلاثة, with taa marbuta written or as haa.
        ("ثلاث سنوات", "3 سنوات"),
        ("ثلاثة أيام", "3 أيام"),
        ("ثلاثه أيام", "3 أيام"),
        ("خمس سنوات", "5 سنوات"),
        ("ثماني ساعات", "8 ساعات"),
        ("عشرة أسابيع", "10 أسابيع"),
        ("عشر سنوات", "10 سنوات"),
        # Teens.
        ("أحد عشر موظفا", "11 موظفا"),
        ("إحدى عشرة سنة", "11 سنة"),
        ("اثنا عشر شهرا", "12 شهرا"),
        ("اثني عشر شهرا", "12 شهرا"),
        ("ثلاثة عشر يوما", "13 يوما"),
        # Units and tens joined by «و».
        ("واحد وعشرون يوما", "21 يوما"),
        ("واحد وعشرين يوما", "21 يوما"),
        ("خمسة وأربعون", "45"),
        ("ثمانية وتسعين", "98"),
        # Hundreds.
        ("مئة", "100"),
        ("مائة ريال", "100 ريال"),
        ("مئتان", "200"),
        ("مائتين", "200"),
        ("ثلاث مئة", "300"),
        ("ثلاثمائة وخمسون ريالا", "350 ريالا"),
        ("خمسمئة", "500"),
        ("مئتان وخمسة", "205"),
        # Thousands.
        ("ألف", "1000"),
        ("ألفان", "2000"),
        ("ألفين", "2000"),
        ("خمسة آلاف", "5000"),
        ("ألف ومئتان", "1200"),
        ("ثلاثة آلاف وخمسمائة", "3500"),
        # Percentages written as words: «المئة» with the article is the percent sign, never a hundred.
        ("خمسة وعشرون بالمئة", "25 بالمئة"),
        ("ثلاثة في المئة", "3 في المئة"),
        ("خمسة بالمائة", "5 بالمائة"),
        # A conjunction or preposition on the first word.
        ("وبعشرة أيام", "10 أيام"),
        ("لثلاثين يوما", "30 يوما"),
        # English.
        ("ninety days", "90 days"),
        ("ten weeks", "10 weeks"),
        ("twenty-one days", "21 days"),
        ("twenty one days", "21 days"),
        ("fifty-five", "55"),
        ("five hundred", "500"),
        ("three hundred and five", "305"),
        ("two thousand", "2000"),
        ("Twelve months", "12 months"),
    ],
)
def test_number_words_fold_to_digits(text, folded):
    assert fold_number_words(text) == folded


@pytest.mark.parametrize(
    "text",
    [
        "مرة واحدة",  # a lone one is more often a pronoun or «مرة واحدة» than a figure
        "one of the reports",
        "no one",
        "أحد الموظفين",  # أحد only before عشر/عشرة
        "يوم الأحد",
        "يوم الاثنين",  # the article: a weekday, not a number
        "آلاف الريالات",  # «آلاف» alone is "thousands"
        "بلغت 812 مليون ريال",  # digits stay digits
        "5.8 بالمئة",
        "يومين في الأسبوع",  # a dual noun is no number word
        "and",
    ],
)
def test_what_is_no_number_word_is_left_as_it_is(text):
    assert fold_number_words(text) == text


def test_lines_and_punctuation_are_kept():
    text = "التجربة\nلا تزيد على تسعين يوما، ويجوز تمديدها مرة واحدة.\nعشرة، خمسة"
    assert fold_number_words(text) == "التجربة\nلا تزيد على 90 يوما، ويجوز تمديدها مرة واحدة.\n10، 5"


def test_numbers_on_two_lines_stay_two_numbers():
    assert fold_number_words("ثلاثة\nأربعة") == "3\n4"


def test_diacritics_and_tatweel_do_not_stop_folding():
    assert fold_number_words("عَشَرَةُ أسابيعَ") == "10 أسابيعَ"
    assert fold_number_words("عشـرين") == "20"
