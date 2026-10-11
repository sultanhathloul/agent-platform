"""Extractors, Arabic restoration and chunking (phase 04, G4): Arabic cases for every
extractor, the normalizer and the chunker, including presentation forms and
visual-order PDF text. Documents are generated here (G3)."""

import pytest

from app.context.tokens import default_counter
from app.rag.bidi import is_visual, restore_line
from app.rag.chunk import chunk_segments
from app.rag.config import load_docs_config
from app.rag.extract import ExtractionFailed, detect_kind, extract
from app.rag.types import Segment
from app.text.arabic import normalize_for_search
from tests.fixtures.documents import (
    LEAVE_REGULATION,
    docx_bytes,
    pdf_bytes,
    scanned_pdf_bytes,
    visual_line,
    xlsx_bytes,
)


@pytest.fixture(scope="module")
def docs_config(config_dir):
    return load_docs_config(config_dir)


# ------------------------------------------------------------------ normalizer and bidi


def test_presentation_forms_fold_to_ordinary_letters():
    assert restore_line("ﺍﻟﺈﺟﺎﺯﺓ") != "الإجازة"[::-1]
    assert normalize_for_search("ﺍﻹﺟﺎﺯﺓ") == normalize_for_search("الإجازة") == "اجازه"


@pytest.mark.parametrize(
    "line",
    [
        "يستحق الموظف إجازة سنوية مدتها ثلاثون يوما.",
        "تمنح إجازة الأمومة لمدة 10 أسابيع في عام 2031 (براتب كامل).",
        "رقم التعميم HR-77 الصادر من إدارة الموارد البشرية",
    ],
)
def test_a_visual_order_line_is_restored_to_logical_order(line):
    visual = visual_line(line)
    assert is_visual(visual)
    assert restore_line(visual) == line


@pytest.mark.parametrize(
    "line",
    ["يستحق الموظف إجازة سنوية مدتها ثلاثون يوما.", "المادة الأولى: تسري هذه اللائحة على الموظفين."],
)
def test_a_logical_line_is_never_reversed(line):
    assert not is_visual(line)
    assert restore_line(line) == line


def test_plain_letters_in_visual_order_are_detected_without_presentation_forms():
    line = "تسري هذه اللائحة على جميع الموظفين في الهيئة"
    assert is_visual(line[::-1])
    assert restore_line(line[::-1]) == line


# ------------------------------------------------------------------ detection


def test_kind_is_detected_by_extension_and_bytes(docs_config):
    assert detect_kind("لائحة.pdf", pdf_bytes([["نص"]], visual=False), docs_config) == "pdf"
    assert detect_kind("لائحة.pdf", b"not a pdf", docs_config) is None
    assert detect_kind("ملف.docx", docx_bytes([("نص", "p")]), docs_config) == "docx"
    assert detect_kind("ملف.docx", xlsx_bytes({"ورقة": [["أ"]]}), docs_config) is None
    assert detect_kind("ملاحظات.md", "# عنوان".encode(), docs_config) == "md"
    assert detect_kind("برنامج.exe", b"MZ", docs_config) is None
    assert detect_kind("نص.txt", b"\x00\x01", docs_config) is None


# ------------------------------------------------------------------ PDF


@pytest.mark.parametrize("visual", [True, False])
def test_pdf_pages_come_back_in_logical_arabic(docs_config, visual):
    document = extract("pdf", pdf_bytes(LEAVE_REGULATION, visual=visual), docs_config)
    assert document.pages == 3
    assert [s.page for s in document.segments] == [1, 2, 3]
    page2 = document.segments[1].text
    assert "تمنح إجازة الأمومة لمدة عشرة أسابيع براتب كامل." in page2
    assert "2031" in document.segments[2].text
    assert not any(0xFE70 <= ord(c) <= 0xFEFF for s in document.segments for c in s.text)


def test_a_scanned_pdf_fails_as_scanned_unsupported(docs_config):
    with pytest.raises(ExtractionFailed) as failure:
        extract("pdf", scanned_pdf_bytes(2), docs_config)
    assert failure.value.reason == "scanned_unsupported"
    assert failure.value.pages == 2


def test_a_pdf_over_the_page_limit_fails(docs_config):
    tight = docs_config.model_copy(
        update={"limits": docs_config.limits.model_copy(update={"max_pdf_pages": 2})}
    )
    with pytest.raises(ExtractionFailed) as failure:
        extract("pdf", pdf_bytes(LEAVE_REGULATION), tight)
    assert failure.value.reason == "too_many_pages"


def test_a_corrupt_pdf_fails_as_corrupt_file(docs_config):
    with pytest.raises(ExtractionFailed) as failure:
        extract("pdf", b"%PDF-1.7\n garbage", docs_config)
    assert failure.value.reason in {"corrupt_file", "empty_document"}


# ------------------------------------------------------------------ DOCX, XLSX, TXT, MD


def test_docx_is_split_by_headings_with_their_path(docs_config):
    data = docx_bytes(
        [
            ("دليل السياسات الداخلية", 0),
            ("الباب الأول: الدوام", 1),
            ("يبدأ الدوام الرسمي الساعة السابعة والنصف صباحا.", "p"),
            ("المادة الثانية: العمل عن بعد", 2),
            ("يسمح بالعمل عن بعد يومين في الأسبوع.", "p"),
            ("الباب الثاني: المكافآت", 1),
            ("تصرف مكافأة الأداء في شهر محرم.", "p"),
        ]
    )
    document = extract("docx", data, docs_config)
    sections = [s.section for s in document.segments]
    assert "دليل السياسات الداخلية › الباب الأول: الدوام › المادة الثانية: العمل عن بعد" in sections
    assert sections[-1] == "دليل السياسات الداخلية › الباب الثاني: المكافآت"
    remote = next(s for s in document.segments if s.section and s.section.endswith("العمل عن بعد"))
    assert "يومين في الأسبوع" in remote.text


def test_xlsx_repeats_the_header_row_in_every_chunk(docs_config):
    rows = [["الاسم", "الإدارة", "الرصيد"]] + [[f"موظف {i}", "إدارة التخطيط", 30 - i % 7] for i in range(400)]
    document = extract("xlsx", xlsx_bytes({"أرصدة الإجازات": rows, "فارغة": []}), docs_config)
    assert [s.section for s in document.segments] == ["أرصدة الإجازات"]
    chunks = chunk_segments(document.segments, default_counter(), max_tokens=200, overlap_tokens=30)
    assert len(chunks) > 3
    assert all(c.text.startswith("الاسم | الإدارة | الرصيد") for c in chunks)
    assert all(c.section == "أرصدة الإجازات" for c in chunks)
    assert all(c.tokens <= 200 for c in chunks)


def test_markdown_and_arabic_structural_headings_split_text(docs_config):
    text = (
        "# تعميم رقم 12\n\nمقدمة التعميم.\n\n## التعليمات\nيلتزم الجميع بالحضور.\n"
        "المادة الثالثة\nتطبق العقوبات وفق النظام.\n"
    )
    document = extract("md", text.encode(), docs_config)
    assert [s.section for s in document.segments] == [
        "تعميم رقم 12",
        "تعميم رقم 12 › التعليمات",
        "تعميم رقم 12 › التعليمات › المادة الثالثة",
    ]


def test_windows_1256_text_is_decoded(docs_config):
    document = extract("txt", "تعميم بشأن ساعات العمل".encode("cp1256"), docs_config)
    assert document.segments[0].text == "تعميم بشأن ساعات العمل"


# ------------------------------------------------------------------ chunker


def test_chunks_respect_the_budget_overlap_and_segment_boundaries():
    counter = default_counter()
    sentence = "يلتزم الموظف بالحضور في الوقت المحدد ويحافظ على سرية المعلومات. "
    segments = [Segment(text=sentence * 120, page=1), Segment(text="صفحة قصيرة.", page=2)]
    chunks = chunk_segments(segments, counter, max_tokens=800, overlap_tokens=120)
    page1 = [c for c in chunks if c.page == 1]
    assert len(page1) >= 2 and all(c.tokens <= 800 for c in chunks)
    assert chunks[-1].page == 2 and chunks[-1].text == "صفحة قصيرة."
    # Consecutive chunks of one page overlap: the second starts with the end of the first.
    assert page1[1].text[:40] in page1[0].text
    assert [c.ord for c in chunks] == list(range(len(chunks)))


def test_a_run_without_separators_is_cut_by_tokens():
    counter = default_counter()
    chunks = chunk_segments([Segment(text="ب" * 5000, page=1)], counter, max_tokens=100, overlap_tokens=10)
    assert len(chunks) > 1 and all(c.tokens <= 100 for c in chunks)
