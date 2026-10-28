"""Contracts of the retrieval library (phase 04, G2): what an extractor produces, what
the chunker stores, what retrieval returns, and whose data a query may touch."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AttachmentStatus = Literal["pending", "indexed", "failed"]

# Why an ingestion failed (ADR-0130). The web shows each with an Arabic message.
FailureReason = Literal[
    "scanned_unsupported",  # a PDF with no text layer (ADR-0128)
    "too_many_pages",
    "unsupported_type",
    "corrupt_file",
    "encrypted",
    "empty_document",
    "storage_unavailable",
    "embedding_unavailable",
    "ingest_error",
    # Hostile or oversized files (owner decisions R3, R4):
    "file_too_large",  # the stored object is over the upload limit
    "archive_unsafe",  # a DOCX or XLSX over the entry, size or ratio limits, or holding an archive
    "xml_unsafe",  # a DOCX or XLSX whose XML declares a DTD or entities
    "stream_too_large",  # a PDF stream that decompresses past its limit
    "text_too_long",  # more extracted text than the limit
    "too_many_chunks",
    "extraction_aborted",  # the parser ran out of time or memory and was stopped
    # The document backend did not take the file (it failed, or went past its own bounds); final:
    # no later status read changes it, and search never returns its chunks (ADR-0254).
    "register_failed",
]

DocumentKind = Literal["pdf", "docx", "xlsx", "txt", "md"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class Owner:
    """Whose data a store call touches. Every VectorStore and attachment query names one."""

    tenant_id: str
    user_id: str


class Segment(_Strict):
    """A unit an extractor produces: text of one PDF page, one DOCX or MD section, or one
    block of spreadsheet rows. A chunk never spans two segments, so every chunk has one
    page or section to cite (ADR-0135)."""

    text: str
    page: int | None = Field(default=None, ge=1)
    section: str | None = None
    # Prepended to every chunk cut from this segment (an XLSX sheet's header row).
    repeat: str | None = None


class ExtractedDocument(_Strict):
    kind: DocumentKind
    pages: int | None = Field(default=None, ge=0, description="PDF pages, None for other kinds.")
    segments: list[Segment]
    # PDF pages under extraction.min_text_chars_per_page: probably scanned, not read (D7, ADR-0201).
    pages_without_text: int = Field(default=0, ge=0)


class Chunk(_Strict):
    ord: int = Field(ge=0, description="Position in the document.")
    text: str
    page: int | None = None
    section: str | None = None
    tokens: int = Field(ge=0)


class ScoredChunk(_Strict):
    """A chunk as retrieval returns it: its place, its text and how it ranked."""

    id: str
    attachment_id: str
    page: int | None = None
    section: str | None = None
    ord: int
    text: str
    score: float = Field(description="The rerank score, or the fused score before rerank.")
    # The collection it was found in: one search may span several (cross-KB retrieval, phase 08).
    collection: str | None = None
    vector_rank: int | None = None
    text_rank: int | None = None
