"""PDF, page by page, with pypdf (BSD-3). Arabic lines come back in logical order with
presentation forms folded (app/rag/bidi.py). A PDF with no text layer fails as
`scanned_unsupported` (ADR-0128); one over the page limit as `too_many_pages`. Bounded
(owner decision R4): a stream that decompresses past `extraction.max_pdf_stream_bytes` fails as
`stream_too_large`, and more than `extraction.max_chars` of text as `text_too_long`, as soon as
the page that passes it is read."""

import io
import logging

from pypdf import PdfReader, apply_configuration
from pypdf.errors import LimitReachedError, PdfReadError

from app.rag.bidi import restore_text
from app.rag.config import DocsConfig
from app.rag.extract.base import ExtractionFailed
from app.rag.types import ExtractedDocument, Segment

logging.getLogger("pypdf").setLevel(logging.ERROR)


class PdfExtractor:
    kind = "pdf"

    def extract(self, data: bytes, config: DocsConfig) -> ExtractedDocument:
        bound = config.extraction.max_pdf_stream_bytes
        # pypdf's own decompression bounds, every filter at the configured one (R4).
        with apply_configuration(
            maximum_declared_stream_length=bound,
            array_based_stream_maximum_output_length=bound,
            jbig2_maximum_output_length=bound,
            lzw_maximum_output_length=bound,
            run_length_maximum_output_length=bound,
            zlib_maximum_output_length=bound,
            brotli_maximum_output_length=bound,
            image_maximum_buffer_size=bound,
        ):
            return self._extract(data, config)

    def _extract(self, data: bytes, config: DocsConfig) -> ExtractedDocument:
        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted and not reader.decrypt(""):
                raise ExtractionFailed("encrypted")
            pages = len(reader.pages)
        except ExtractionFailed:
            raise
        except LimitReachedError as exc:
            raise ExtractionFailed("stream_too_large", detail=type(exc).__name__) from exc
        except (PdfReadError, ValueError, KeyError, TypeError, OSError) as exc:
            raise ExtractionFailed("corrupt_file", detail=type(exc).__name__) from exc
        if pages == 0:
            raise ExtractionFailed("empty_document", pages=0)
        if pages > config.limits.max_pdf_pages:
            raise ExtractionFailed("too_many_pages", pages=pages)
        segments: list[Segment] = []
        characters = extracted = 0
        for number, page in enumerate(reader.pages, start=1):
            try:
                # Decoded first: extract_text swallows a decompression limit and returns no text,
                # which would read as a scanned page (R4).
                contents = page.get_contents()
                if contents is not None:
                    contents.get_data()
                raw = page.extract_text() or ""
            except LimitReachedError as exc:
                raise ExtractionFailed("stream_too_large", pages=pages, detail=type(exc).__name__) from exc
            except Exception as exc:  # a broken content stream on one page
                raise ExtractionFailed("corrupt_file", pages=pages, detail=type(exc).__name__) from exc
            text = restore_text(raw).strip()
            characters += sum(not c.isspace() for c in text)
            extracted += len(text)
            if extracted > config.extraction.max_chars:
                raise ExtractionFailed("text_too_long", pages=pages)
            if text:
                segments.append(Segment(text=text, page=number))
        if characters < config.extraction.min_text_chars_per_page * pages:
            raise ExtractionFailed("scanned_unsupported", pages=pages)
        return ExtractedDocument(kind="pdf", pages=pages, segments=segments)
