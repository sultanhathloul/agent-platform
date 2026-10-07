"""The DATA sentence buffer (I5): flush on . ? ؟ ! ؛ followed by whitespace, on a newline,
or at 240 characters, never inside a number, an email or a decimal."""

import pytest

from app.graph.streaming import SentenceBuffer, split_sentences

TERMINATORS = [".", "?", "؟", "!", "؛"]


def feed(chunks: list[str], flush: int = 240) -> list[str]:
    buffer = SentenceBuffer(TERMINATORS, flush)
    out = [s for chunk in chunks for s in buffer.feed(chunk)]
    rest = buffer.flush()
    return [*out, rest] if rest else out


@pytest.mark.parametrize(
    ("text", "sentences"),
    [
        ("الرياض عاصمة المملكة. وجدة مدينة ساحلية.", ["الرياض عاصمة المملكة. ", "وجدة مدينة ساحلية."]),
        ("هل تعرف الرياض؟ نعم أعرفها!", ["هل تعرف الرياض؟ ", "نعم أعرفها!"]),
        ("أولًا؛ ثانيًا", ["أولًا؛ ", "ثانيًا"]),
        ("سطر أول\nسطر ثانٍ", ["سطر أول\n", "سطر ثانٍ"]),
        ("Riyadh is the capital. Jeddah is a port.", ["Riyadh is the capital. ", "Jeddah is a port."]),
    ],
)
def test_terminators_followed_by_whitespace_and_newlines_end_sentences(text, sentences):
    assert split_sentences(text, TERMINATORS, 240) == sentences


@pytest.mark.parametrize(
    "text",
    [
        "النسبة 3.5 في المئة",
        "راسلني على test.user@example.org اليوم",
        "الرقم 050.123.4567 للتواصل",
        "الموقع example.org مفيد",
    ],
)
def test_a_dot_inside_a_number_an_address_or_a_domain_does_not_cut(text):
    assert split_sentences(text, TERMINATORS, 240) == [text]


def test_a_terminator_at_the_end_of_a_chunk_waits_for_the_next_character():
    # "x." alone cannot be judged until the next character arrives.
    assert feed(["بريدي test.", "user@example.org. شكرًا"]) == ["بريدي test.user@example.org. ", "شكرًا"]


def test_a_number_split_across_chunks_is_checked_whole():
    chunks = ["رقمي 05", "01 23", "4 567. وشكرًا"]
    assert feed(chunks) == ["رقمي 0501 234 567. ", "وشكرًا"]


def test_a_long_sentence_is_cut_at_the_limit_at_the_last_space_outside_a_number():
    words = "كلمة " * 46  # 230 characters
    text = words + "رقم 0501 234 567 ثم نهاية الجملة بلا علامة"
    first, *rest = feed([text])
    assert len(first) <= 240
    assert "0501 234 567" in "".join(rest) or "0501 234 567" in first
    assert not first.rstrip().endswith(("0501", "234"))  # never between two digit groups
    assert "".join([first, *rest]) == text


def test_a_long_run_without_spaces_is_cut_at_the_limit():
    text = "ا" * 500
    parts = feed([text])
    assert [len(p) for p in parts] == [240, 240, 20]


def test_concatenation_is_lossless():
    text = "الأول. الثاني؟ الثالث!\nالرابع؛ الخامس 3.5 والبريد a.b@example.org. النهاية"
    assert "".join(split_sentences(text, TERMINATORS, 240)) == text
    assert "".join(feed([text[i : i + 3] for i in range(0, len(text), 3)])) == text


# ------------------------------------------------------------------ digit runs (review round 1, ADR-0111)


def _whole_in_one(parts: list[str], number: str) -> bool:
    return any(number in part for part in parts)


@pytest.mark.parametrize("number", ["050 - 123 - 4567", "٠٥٠ - ١٢٣ - ٤٥٦٧", "050 123 4567", "050٫123٬4567"])
def test_a_joined_number_across_the_240_boundary_stays_in_one_sentence(number):
    for offset in range(-len(number), 2):  # the number straddles the limit at every position
        prefix = "ك" * (240 + offset - 1) + " "
        text = prefix + number + " ثم بقية الكلام"
        for size in (1, 3, 7, len(text)):
            parts = feed([text[i : i + size] for i in range(0, len(text), size)])
            assert "".join(parts) == text
            assert _whole_in_one(parts, number), (offset, size, parts)


def test_a_buffer_ending_in_a_space_after_a_digit_waits_for_the_next_chunk():
    buffer = SentenceBuffer(TERMINATORS, 240)
    head = "ك" * 234 + " 0501 "  # exactly 240 characters, the last one a space after a digit
    assert len(head) == 240 and buffer.feed(head) == []
    released = buffer.feed("234 567 ونهاية")
    rest = buffer.flush()
    assert _whole_in_one([*released, rest], "0501 234 567")


def test_a_sentence_end_inside_a_spaced_number_does_not_cut():
    assert split_sentences("الرقم 050. 123 4567 للتواصل.", TERMINATORS, 240) == [
        "الرقم 050. 123 4567 للتواصل."
    ]
    # A digit before the terminator and a word after it is an ordinary sentence end.
    assert split_sentences("في عام 2024. بدأ المشروع.", TERMINATORS, 240) == ["في عام 2024. ", "بدأ المشروع."]
