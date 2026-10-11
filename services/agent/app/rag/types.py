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
    vector_rank: int | None = None
    text_rank: int | None = None
