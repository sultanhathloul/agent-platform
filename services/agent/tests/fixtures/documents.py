"""Generated test documents (phase 04, G3): fictional Arabic regulations, circulars and a
CV with fake PII, never a real or ministry document (ADR-0065). PDFs are drawn with an
Arabic-capable TrueType font (ARABIC_TEST_FONT; the test image installs DejaVu).

`visual=True` draws each Arabic line the way many real PDFs store it: shaped into
presentation forms and reversed into visual order, so the extractor has to restore it
(ADR-0135). `visual=False` stores the logical text unchanged."""

import io
import os
import re
from collections.abc import Sequence

FONT_ENV = "ARABIC_TEST_FONT"
_LTR = re.compile(r"[0-9A-Za-z][0-9A-Za-z.,:/%@_\-]*")


def font_path() -> str:
    path = os.environ.get(FONT_ENV, "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    if not os.path.isfile(path):
        raise RuntimeError(f"no Arabic test font at {path}; set {FONT_ENV}")
    return path


def visual_line(line: str) -> str:
    """Shaped presentation forms in visual order, with digit and Latin runs kept LTR."""
    import arabic_reshaper

    shaped = arabic_reshaper.reshape(line)
    reversed_line = shaped[::-1].translate(str.maketrans("()[]", ")(]["))
    return _LTR.sub(lambda m: m.group(0)[::-1], reversed_line)


def pdf_bytes(pages: Sequence[Sequence[str]], *, visual: bool = True) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    if "ArabicTest" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("ArabicTest", font_path()))
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4, invariant=1)
    width, height = A4
    for lines in pages:
        pdf.setFont("ArabicTest", 12)
        y = height - 60
        for line in lines:
            pdf.drawRightString(width - 50, y, visual_line(line) if visual else line)
            y -= 20
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def scanned_pdf_bytes(pages: int = 2) -> bytes:
    """Pages that carry only an image of text: no text layer (ADR-0128)."""
    from PIL import Image, ImageDraw
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    image = Image.new("RGB", (400, 200), "white")
    draw = ImageDraw.Draw(image)
    for row in range(8):
        draw.rectangle([20, 20 + row * 22, 380, 32 + row * 22], fill="black")
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4, invariant=1)
    for _ in range(pages):
        pdf.drawImage(ImageReader(image), 50, 400, width=400, height=200)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def docx_bytes(blocks: Sequence[tuple[str, str | int]]) -> bytes:
    """(text, level) pairs: level 0 is the title, 1–3 headings, "p" a paragraph."""
    from docx import Document

    document = Document()
    for text, level in blocks:
        if level == "p":
            document.add_paragraph(text)
        else:
            document.add_heading(text, level=int(level))
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def xlsx_bytes(sheets: dict[str, Sequence[Sequence[object]]]) -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    workbook.remove(workbook.active)
    for title, rows in sheets.items():
        sheet = workbook.create_sheet(title)
        for row in rows:
            sheet.append(list(row))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


# ------------------------------------------------------------------ fictional content

LEAVE_REGULATION = [
    [
        "لائحة الإجازات في هيئة الواحة للخدمات الرقمية",
        "المادة الأولى: تسري هذه اللائحة على جميع موظفي الهيئة.",
        "المادة الثانية: يستحق الموظف إجازة سنوية مدتها ثلاثون يوما.",
    ],
    [
        "المادة الثالثة: تمنح إجازة الأمومة لمدة عشرة أسابيع براتب كامل.",
        "المادة الرابعة: يحق للموظف إجازة اضطرارية مدتها خمسة أيام في السنة.",
    ],
    [
        "المادة الخامسة: ترفع طلبات الإجازة عبر بوابة الخدمات الذاتية.",
        "المادة السادسة: يعمل بهذه اللائحة اعتبارا من عام 2031.",
    ],
]
