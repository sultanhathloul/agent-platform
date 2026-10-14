"""Builds the committed PDF fixtures of the Arabic extraction tests (owner decision A2). Fake
data only (G3). Run once, on a host with LibreOffice, from services/agent:

    uv run --with python-bidi==0.6.6 --with arabic-reshaper --with reportlab \\
        python tests/fixtures/pdf/make_fixtures.py

- `mixed.docx` → `libreoffice-mixed.pdf`: an Arabic document as most producers write it,
  exported by LibreOffice (`soffice --headless --convert-to pdf`): right-to-left paragraphs
  with digits, Latin, a label before a national ID and before an IBAN, and a table;
- `visual-order.pdf`: the lines shaped with arabic_reshaper and put in visual order with
  python-bidi's `get_display` (the Unicode bidirectional algorithm, an implementation this
  repository does not contain), drawn by reportlab as one run each, as older producers wrote
  Arabic. The extraction tests expect the logical lines back; nothing in them is built as the
  inverse of the code under test."""

import os
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent

MIXED_LINES = [
    "لائحة الإجازات في هيئة الواحة للخدمات الرقمية",
    "الإصدار الثالث لعام 2031",
    "يعمل الموظف من عام 2027 إلى عام 2030 في مشروع Oasis Cloud.",
    "رقم الهوية الوطنية: 1012345672",
    "رقم الآيبان: SA0380000000608010167519",
    "قليلا جدا",
    "فعلا قليلا مثلا",
]
MIXED_TABLE = [["البند", "المبلغ", "العام"], ["التدريب", "120000", "2031"]]

VISUAL_LINES = [
    "تعميم داخلي رقم HR-77 بشأن الدوام",
    "تبدأ ساعات العمل الصيفية من عام 2027 إلى عام 2030 الساعة 7:30 صباحا.",
    "رقم الجوال: 0551234567",
    "قليلا جدا",
]


def _rtl(paragraph) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement

    paragraph._p.get_or_add_pPr().append(OxmlElement("w:bidi"))
    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    for run in paragraph.runs:
        run._r.get_or_add_rPr().append(OxmlElement("w:rtl"))


def mixed_docx(path: Path) -> None:
    from docx import Document

    document = Document()
    for line in MIXED_LINES:
        _rtl(document.add_paragraph(line))
    table = document.add_table(rows=len(MIXED_TABLE), cols=len(MIXED_TABLE[0]))
    for r, row in enumerate(MIXED_TABLE):
        for c, value in enumerate(row):
            table.cell(r, c).text = value
            for paragraph in table.cell(r, c).paragraphs:
                _rtl(paragraph)
    document.save(path)


def visual_pdf(path: Path) -> None:
    import arabic_reshaper
    from bidi.algorithm import get_display
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    font = os.environ.get("ARABIC_TEST_FONT", "/Library/Fonts/Arial Unicode.ttf")
    pdfmetrics.registerFont(TTFont("Arabic", font))
    pdf = canvas.Canvas(str(path), pagesize=A4, invariant=1)
    width, height = A4
    pdf.setFont("Arabic", 12)
    y = height - 60
    for line in VISUAL_LINES:
        pdf.drawRightString(width - 50, y, get_display(arabic_reshaper.reshape(line)))
        y -= 20
    pdf.showPage()
    pdf.save()


def main() -> None:
    mixed_docx(HERE / "mixed.docx")
    subprocess.run(
        ["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(HERE), str(HERE / "mixed.docx")],
        check=True,
        capture_output=True,
    )
    (HERE / "mixed.pdf").rename(HERE / "libreoffice-mixed.pdf")
    visual_pdf(HERE / "visual-order.pdf")


if __name__ == "__main__":
    main()
