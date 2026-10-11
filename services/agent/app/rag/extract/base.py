"""Extractors (phase 04, ADR-0135): bytes of one file to an ExtractedDocument. One
extractor per document kind behind the `Extractor` protocol; OCR for scanned PDFs is
v2 and registers here without touching the pipeline (ADR-0128)."""

from typing import Protocol

from app.rag.config import DocsConfig
from app.rag.types import DocumentKind, ExtractedDocument, FailureReason


class ExtractionFailed(Exception):
    """The file cannot be indexed, for a reason the user is shown (ADR-0130)."""

    def __init__(self, reason: FailureReason, *, pages: int | None = None, detail: str = ""):
        super().__init__(detail or reason)
        self.reason = reason
        self.pages = pages


class Extractor(Protocol):
    kind: DocumentKind

    def extract(self, data: bytes, config: DocsConfig) -> ExtractedDocument: ...


_ZIP = b"PK\x03\x04"


def detect_kind(name: str, data: bytes, config: DocsConfig) -> DocumentKind | None:
    """The file's kind when its extension is an allowed type and its bytes agree with it."""
    kind = config.kind_of(name)
    if kind == "pdf":
        return kind if data[:1024].lstrip().startswith(b"%PDF") else None
    if kind in ("docx", "xlsx"):
        marker = b"word/" if kind == "docx" else b"xl/"
        return kind if data.startswith(_ZIP) and marker in data else None
    if kind in ("txt", "md"):
        return kind if b"\x00" not in data[:8192] else None
    return None
