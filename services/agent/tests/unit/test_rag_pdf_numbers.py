"""Numbers a PDF draws right to left, piece by piece, inside Arabic text (ADR-0411). The Q2 2026
labour-market bulletin drew «6.5» as «5», «.», «6» and pypdf read «5.6»; 2026 read «6202», 0.1 read «10.».
The extractor reads such a number in the order of its pieces' positions, and leaves alone what is not one
number: a table row's cells drawn right to left, a sentence's full stop, a run of axis labels."""

import re

import pytest

from app.rag.bidi import reversed_numbers
from app.rag.config import load_docs_config
from app.rag.extract import extract, extractor_version
from tests.fixtures.documents import rtl_pieces_pdf_bytes


@pytest.fixture
def docs_config(config_dir):
    return load_docs_config(config_dir)


def text_of(lines, docs_config, **kwargs) -> str:
    return extract("pdf", rtl_pieces_pdf_bytes(lines, **kwargs), docs_config).segments[0].text


def test_decimals_percentages_and_years_drawn_right_to_left_read_as_printed(docs_config):
    text = text_of([
        ["وقد بلغ معدل البطالة للسعوديين", "6.5", "% في الربع الثاني من عام", "2026"],
        ["بارتفاع مقداره", "0.1", "نقطة مئوية وبانخفاض سنوي بمقدار", "0.3", "نقطة مئوية"],
        ["ويبلغ عدد السكان", "35,300,280", "نسمة"],
    ], docs_config)
    assert re.search(r"للسعوديين 6\.5 ?%", text) and "عام 2026" in text
    assert "مقداره 0.1 نقطة" in text and "بمقدار 0.3 نقطة" in text and "35,300,280" in text
    assert "5.6" not in text and "6202" not in text and "10." not in text


def test_a_range_keeps_its_two_ends_in_order(docs_config):
    text = text_of([["الفئة العمرية من", "15", "إلى", "24", "سنة، والفترة", "2021", "-", "2025"]], docs_config)
    assert "من 15 إلى 24 سنة" in text and "2021" in text and "2025" in text
    assert text.index("2021") < text.index("2025")


def test_a_table_rows_cells_keep_their_order_and_a_full_stop_stays_at_the_end(docs_config):
    # Cells drawn right to left far apart are separate numbers: the row keeps the Arabic header's order.
    row = text_of([["جدول نسبة المدخنين حسب الجنس والجنسية في المملكة"], ["نسبة المدخنين", "|26.74", "|3.33", "|23.57", "|4.99"]],
                  docs_config)
    assert row.index("26.74") < row.index("3.33") < row.index("23.57") < row.index("4.99")
    end = text_of([["جدول نسبة المدخنين حسب الجنس والجنسية في المملكة"], ["وبلغت النسبة", "78.8", "."]], docs_config)
    assert "78.8" in end and ".78.8" not in end


def test_pieces_far_apart_or_too_long_are_not_one_number():
    # (x, y, text, size) in stream order: two cells 90 points apart; axis years drawn edge to edge.
    cells = [(370.1, 280.8, "26.74", 11.0), (279.6, 280.8, "3.33", 11.0)]
    assert reversed_numbers(cells) == []
    axis = [(500.0, 100.0, "2025", 10.0), (476.0, 100.0, "2024", 10.0), (452.0, 100.0, "2023", 10.0),
            (428.0, 100.0, "2022", 10.0)]
    assert reversed_numbers(axis) == []
    bulletin = [(455.9, 758.8, "5", 9.96), (454.2, 758.8, ".", 9.96), (449.3, 758.8, "6", 9.96)]
    assert reversed_numbers(bulletin) == [(["5", ".", "6"], "6.5")]
    left_to_right = [(347.2, 767.4, "67.", 9.96), (358.6, 767.4, "2", 9.96)]
    assert reversed_numbers(left_to_right) == []


def test_the_pdf_extractor_version_moves_so_the_seed_reindexes():
    assert extractor_version("pdf") == "2" and extractor_version("docx") == "1"
