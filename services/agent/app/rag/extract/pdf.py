"""PDF, page by page, with pypdf (BSD-3). Arabic lines come back in logical order with
presentation forms folded (app/rag/bidi.py). A PDF with no text layer fails as
`scanned_unsupported` (ADR-0128); one over the page limit as `too_many_pages`."""

import io
import logging

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.rag.bidi import restore_text
from app.rag.config import DocsConfig
from app.rag.extract.base import ExtractionFailed
from app.rag.types import ExtractedDocument, Segment

logging.getLogger("pypdf").setLevel(logging.ERROR)


class PdfExtractor:
    kind = "pdf"

    def extract(self, data: bytes, config: DocsConfig) -> ExtractedDocument:
        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted and not reader.decrypt(""):
                raise ExtractionFailed("encrypted")
            pages = len(reader.pages)
        except ExtractionFailed:
            raise
        except (PdfReadError, ValueError, KeyError, TypeError, OSError) as exc:
            raise ExtractionFailed("corrupt_file", detail=type(exc).__name__) from exc
        if pages == 0:
            raise ExtractionFailed("empty_document", pages=0)
        if pages > config.limits.max_pdf_pages:
            raise ExtractionFailed("too_many_pages", pages=pages)
        segments: list[Segment] = []
        characters = 0
        for number, page in enumerate(reader.pages, start=1):
            try:
                raw = page.extract_text() or ""
            except Exception as exc:  # a broken content stream on one page
                raise ExtractionFailed("corrupt_file", pages=pages, detail=type(exc).__name__) from exc
            text = restore_text(raw).strip()
            characters += sum(not c.isspace() for c in text)
            if text:
                segments.append(Segment(text=text, page=number))
        if characters < config.extraction.min_text_chars_per_page * pages:
            raise ExtractionFailed("scanned_unsupported", pages=pages)
        return ExtractedDocument(kind="pdf", pages=pages, segments=segments)
