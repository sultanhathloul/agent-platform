"""config/docs.yaml: the document backend, limits, chunking, ingestion, the orphan sweeper and
retrieval (phase 04; ADR-0126, ADR-0133, ADR-0159). The gateway reads the same
file for the upload limits, so the two services cannot disagree. Page attribution is
capability-agnostic and lives in config/citations.yaml (owner decision C1)."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.rag.types import DocumentKind


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FileType(_Strict):
    extensions: list[str]
    mimes: list[str]


class Limits(_Strict):
    max_file_bytes: int = Field(gt=0)
    max_files_per_conversation: int = Field(gt=0)
    max_pdf_pages: int = Field(gt=0)
    types: dict[DocumentKind, FileType]


class Chunking(_Strict):
    max_tokens: int = Field(gt=0)
    overlap_tokens: int = Field(ge=0)
    max_chunks: int = Field(gt=0)  # per file; more ends failed with too_many_chunks (R4)

    @model_validator(mode="after")
    def _overlap(self) -> "Chunking":
        if self.overlap_tokens >= self.max_tokens:
            raise ValueError("overlap_tokens must be below max_tokens")
        return self


class Extraction(_Strict):
    min_text_chars_per_page: int = Field(ge=0)
    max_chars: int = Field(gt=0)  # extracted text per file; more ends failed with text_too_long (R4)
    max_pdf_stream_bytes: int = Field(gt=0)  # one PDF stream, decompressed (stream_too_large)


class Archive(_Strict):
    """Checked before a DOCX or XLSX is parsed (owner decision R4): archive_unsafe past these."""

    max_entries: int = Field(gt=0)
    max_uncompressed_bytes: int = Field(gt=0)
    max_ratio: float = Field(gt=1)


class Sandbox(_Strict):
    """Each file is parsed in a child process with these limits (owner decision R4)."""

    cpu_seconds: int = Field(gt=0)
    memory_mb: int = Field(gt=0)
    timeout_seconds: float = Field(gt=0)


class Ingest(_Strict):
    workers: int = Field(ge=1)
    embed_batch: int = Field(ge=1)
    max_attempts: int = Field(ge=1)
    sandbox: Sandbox


class Rerank(_Strict):
    """The cross-encoder step (owner decisions R2, Q1): on or off; how many fused candidates it
    scores; and how much of each, in characters (None: the whole chunk)."""

    enabled: bool
    candidates: int = Field(gt=0)
    max_chars: int | None = Field(default=None, gt=0)


class Retrieval(_Strict):
    vector_top_k: int = Field(gt=0)
    text_top_k: int = Field(gt=0)
    rrf_k: int = Field(gt=0)
    rerank: Rerank
    top_k: int = Field(gt=0)
    stopwords: dict[str, list[str]] = {}


class OrphanSweeper(_Strict):
    min_age_minutes: float = Field(gt=0)
    interval_minutes: float = Field(gt=0)


class DocsConfig(_Strict):
    # Which DocumentBackend serves documents (C6): the phase 04 pipeline, `internal`, for now.
    backend: Literal["internal"]
    limits: Limits
    chunking: Chunking
    extraction: Extraction
    archive: Archive
    ingest: Ingest
    retrieval: Retrieval
    orphan_sweeper: OrphanSweeper

    def kind_of(self, name: str) -> DocumentKind | None:
        suffix = Path(name).suffix.lower().lstrip(".")
        for kind, spec in self.limits.types.items():
            if suffix in spec.extensions:
                return kind
        return None


def load_docs_config(config_dir: Path) -> DocsConfig:
    return DocsConfig.model_validate(yaml.safe_load((config_dir / "docs.yaml").read_text(encoding="utf-8")))
