"""DOCX by headings, with python-docx (MIT): body paragraphs and tables in document
order; a Heading or Title paragraph starts a new section, named by the heading path
(e.g. «الباب الأول › المادة الثالثة»)."""

import io
import unicodedata
import zipfile

from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.rag.config import DocsConfig
from app.rag.extract.base import ExtractionFailed
from app.rag.types import ExtractedDocument, Segment

_SEPARATOR = " › "


def _heading_level(paragraph: Paragraph) -> int | None:
    style = (paragraph.style.style_id or "") if paragraph.style is not None else ""
    name = (paragraph.style.name or "") if paragraph.style is not None else ""
    for label in (style, name):
        if label.lower() == "title":
            return 0
        lowered = label.lower().replace(" ", "")
        if lowered.startswith("heading") and lowered[7:].isdigit():
            return int(lowered[7:])
    return None


class DocxExtractor:
    kind = "docx"

    def extract(self, data: bytes, config: DocsConfig) -> ExtractedDocument:
        try:
            document = Document(io.BytesIO(data))
        except (PackageNotFoundError, zipfile.BadZipFile, KeyError, ValueError) as exc:
            raise ExtractionFailed("corrupt_file", detail=type(exc).__name__) from exc
        path: list[tuple[int, str]] = []
        segments: list[Segment] = []
        lines: list[str] = []

        def flush() -> None:
            text = "\n".join(lines).strip()
            if text:
                section = _SEPARATOR.join(title for _, title in path) or None
                segments.append(Segment(text=text, section=section))
            lines.clear()

        for item in document.iter_inner_content():
            if isinstance(item, Paragraph):
                text = unicodedata.normalize("NFKC", item.text).strip()
                if not text:
                    continue
                level = _heading_level(item)
                if level is not None:
                    flush()
                    while path and path[-1][0] >= level:
                        path.pop()
                    path.append((level, text))
                    lines.append(text)
                else:
                    lines.append(text)
            elif isinstance(item, Table):
                for row in item.rows:
                    cells = [unicodedata.normalize("NFKC", c.text).strip() for c in row.cells]
                    if any(cells):
                        lines.append(" | ".join(cells))
        flush()
        if not segments:
            raise ExtractionFailed("empty_document")
        return ExtractedDocument(kind="docx", segments=segments)
