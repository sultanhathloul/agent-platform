"""Extractor registry (ADR-0135): one per document kind. OCR registers here in v2."""

from app.rag.config import DocsConfig
from app.rag.extract.base import ExtractionFailed, Extractor, detect_kind
from app.rag.extract.docx import DocxExtractor
from app.rag.extract.pdf import PdfExtractor
from app.rag.extract.text import TextExtractor
from app.rag.extract.xlsx import XlsxExtractor
from app.rag.types import DocumentKind, ExtractedDocument

EXTRACTORS: dict[str, Extractor] = {
    "pdf": PdfExtractor(),
    "docx": DocxExtractor(),
    "xlsx": XlsxExtractor(),
    "txt": TextExtractor("txt"),
    "md": TextExtractor("md"),
}


def extract(kind: DocumentKind, data: bytes, config: DocsConfig) -> ExtractedDocument:
    return EXTRACTORS[kind].extract(data, config)


__all__ = ["EXTRACTORS", "ExtractionFailed", "Extractor", "detect_kind", "extract"]


def extractor_version(kind: str) -> str:
    """The version of a kind's extractor; a KB document indexed by another is re-indexed by the seed (ADR-0410)."""
    from app.rag.extract import pdf, xlsx

    return {"xlsx": xlsx.VERSION, "pdf": pdf.VERSION}.get(kind, "1")
