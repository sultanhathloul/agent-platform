"""Chunking and the XLSX header (owner decision A3): no chunk is longer than its budget, the
overlap included; a header longer than the budget is truncated and never squeezes out the
rows; title rows above an XLSX table are not its header. Arabic text throughout (G4)."""

from app.context.tokens import default_counter
from app.rag.chunk import chunk_segments
from app.rag.config import load_docs_config
from app.rag.extract import extract
from app.rag.types import Segment
from tests.fixtures.documents import xlsx_bytes

import pytest


@pytest.fixture(scope="module")
def docs_config(config_dir):
    return load_docs_config(config_dir)


class _Words:
    """One token per word: chunk sizes a test can state exactly."""

    def count(self, text: str) -> int:
        return len(text.split())


def test_the_overlap_gives_way_so_no_chunk_passes_the_budget():
    """Owner decision A3: after a cut, the overlap (25) plus the next unit (190) would make 215."""
    units = [" ".join(["أ"] * 100), " ".join(["ب"] * 25), " ".join(["ج"] * 190)]
    chunks = chunk_segments(
        [Segment(text="\n".join(units), page=1)], _Words(), max_tokens=200, overlap_tokens=30
    )
    assert [c.tokens for c in chunks] == [125, 190]
    assert all(c.tokens <= 200 for c in chunks)


def test_a_header_longer_than_the_budget_is_truncated_and_rows_still_fit():
    header = " ".join(f"عمود{i}" for i in range(300))
    rows = "\n".join(f"صف {i} قيمة" for i in range(60))
    chunks = chunk_segments(
        [Segment(text=rows, section="ورقة", repeat=header)], _Words(), max_tokens=200, overlap_tokens=30
    )
    assert all(c.tokens <= 200 for c in chunks)
    assert all(c.text.startswith("عمود0 عمود1") for c in chunks)
    assert len(chunks) < 10  # each chunk holds rows: the budget did not collapse to one token
    assert sum(c.text.count("صف ") for c in chunks) >= 60


def test_real_token_counts_stay_within_the_budget_on_arabic_text():
    counter = default_counter()
    paragraphs = [
        ("يلتزم الموظف بالحضور في الوقت المحدد ويحافظ على سرية المعلومات " * n).strip()
        for n in (1, 9, 2, 14, 1, 13)
    ]
    chunks = chunk_segments(
        [Segment(text="\n\n".join(paragraphs), page=1)], counter, max_tokens=200, overlap_tokens=30
    )
    assert all(c.tokens <= 200 for c in chunks)


def test_xlsx_title_rows_above_the_table_are_not_the_header(docs_config):
    """Owner decision A3: the header is the first row followed by a data row of its width."""
    rows = [
        ["ميزانية هيئة الواحة لعام 2031"],
        ["أُعدت في 1 محرم"],
        ["البند", "المبلغ", "الملاحظة"],
        ["التدريب", 120000, "دورات داخلية"],
        ["السفر", 45000, "مهام رسمية"],
    ]
    [segment] = extract("xlsx", xlsx_bytes({"الميزانية": rows}), docs_config).segments
    assert segment.repeat == "البند | المبلغ | الملاحظة"
    assert segment.text.split("\n") == [
        "ميزانية هيئة الواحة لعام 2031",
        "أُعدت في 1 محرم",
        "التدريب | 120000 | دورات داخلية",
        "السفر | 45000 | مهام رسمية",
    ]


def test_a_sheet_without_a_table_takes_its_first_row(docs_config):
    [segment] = extract(
        "xlsx", xlsx_bytes({"ملاحظات": [["ملاحظة أولى"], ["ملاحظة ثانية"]]}), docs_config
    ).segments
    assert segment.repeat == "ملاحظة أولى" and segment.text == "ملاحظة ثانية"
