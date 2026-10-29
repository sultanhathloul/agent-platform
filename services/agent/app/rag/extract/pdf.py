"""PDF, page by page, with pypdf (BSD-3). Arabic lines come back in logical order with
presentation forms folded (app/rag/bidi.py). A PDF with no text layer fails as
`scanned_unsupported` (ADR-0128); one over the page limit as `too_many_pages`. A PDF that passes
the threshold on average but holds pages under it is indexed from its text pages and counts the
others (`pages_without_text`, D7, ADR-0201); they are not read (OCR is v2). Bounded
(owner decision R4): a stream that decompresses past `extraction.max_pdf_stream_bytes` fails as
`stream_too_large`, and more than `extraction.max_chars` of text as `text_too_long`, as soon as
the page that passes it is read. A number a producer drew right to left in pieces («5», «.», «6» for 6.5,
inside Arabic text) is read in the order of its pieces' positions (ADR-0411); extractor version 2."""

import io
import logging

from pypdf import PdfReader, apply_configuration
from pypdf.errors import LimitReachedError, PdfReadError

from app.rag.bidi import restore_numbers, restore_text, reversed_numbers
from app.rag.config import DocsConfig
from app.rag.extract.base import ExtractionFailed
from app.rag.types import ExtractedDocument, Segment

logging.getLogger("pypdf").setLevel(logging.ERROR)

# Bumped when the text a page gives changes, so the seed re-indexes what an older version cut (ADR-0410).
VERSION = "2"


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
        characters = extracted = without_text = 0
        threshold = config.extraction.min_text_chars_per_page
        for number, page in enumerate(reader.pages, start=1):
            try:
                # Decoded first: extract_text swallows a decompression limit and returns no text,
                # which would read as a scanned page (R4).
                contents = page.get_contents()
                if contents is not None:
                    contents.get_data()
                drawn: list[tuple[float, float, str, float]] = []
                raw = page.extract_text(
                    visitor_text=lambda text, cm, tm, font, size: drawn.append(
                        (tm[4], tm[5], text, abs(size * tm[0]) or abs(size))
                    )
                ) or ""
                # A number drawn right to left, piece by piece, read in position order (ADR-0411).
                raw = restore_numbers(raw, reversed_numbers(drawn))
            except LimitReachedError as exc:
                raise ExtractionFailed("stream_too_large", pages=pages, detail=type(exc).__name__) from exc
            except Exception as exc:  # a broken content stream on one page
                raise ExtractionFailed("corrupt_file", pages=pages, detail=type(exc).__name__) from exc
            text = restore_text(raw).strip()
            on_page = sum(not c.isspace() for c in text)
            characters += on_page
            if on_page < threshold:
                without_text += 1
            extracted += len(text)
            if extracted > config.extraction.max_chars:
                raise ExtractionFailed("text_too_long", pages=pages)
            if text:
                segments.append(Segment(text=text, page=number))
        if characters < threshold * pages:
            raise ExtractionFailed("scanned_unsupported", pages=pages)
        return ExtractedDocument(kind="pdf", pages=pages, segments=segments, pages_without_text=without_text)
