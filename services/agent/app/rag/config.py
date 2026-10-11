"""config/docs.yaml: limits, retention, chunking, ingestion, retrieval and page
attribution (phase 04; ADR-0125, ADR-0126, ADR-0133, ADR-0140). The gateway reads the
same file for the upload limits, so the two services cannot disagree."""

from pathlib import Path

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

    @model_validator(mode="after")
    def _overlap(self) -> "Chunking":
        if self.overlap_tokens >= self.max_tokens:
            raise ValueError("overlap_tokens must be below max_tokens")
        return self


class Extraction(_Strict):
    min_text_chars_per_page: int = Field(ge=0)


class Ingest(_Strict):
    workers: int = Field(ge=1)
    embed_batch: int = Field(ge=1)
    max_attempts: int = Field(ge=1)


class Retrieval(_Strict):
    vector_top_k: int = Field(gt=0)
    text_top_k: int = Field(gt=0)
    rrf_k: int = Field(gt=0)
    rerank_candidates: int = Field(gt=0)
    top_k: int = Field(gt=0)
    evidence_max_tokens: int = Field(gt=0)
    stopwords: dict[str, list[str]] = {}


class Citations(_Strict):
    ngram: int = Field(ge=1)
    min_shared_ngrams: int = Field(ge=1)


class Purge(_Strict):
    interval_hours: float = Field(gt=0)


class DocsConfig(_Strict):
    retention_days: int = Field(gt=0)
    limits: Limits
    chunking: Chunking
    extraction: Extraction
    ingest: Ingest
    retrieval: Retrieval
    citations: Citations
    purge: Purge

    def kind_of(self, name: str) -> DocumentKind | None:
        suffix = Path(name).suffix.lower().lstrip(".")
        for kind, spec in self.limits.types.items():
            if suffix in spec.extensions:
                return kind
        return None


def load_docs_config(config_dir: Path) -> DocsConfig:
    return DocsConfig.model_validate(yaml.safe_load((config_dir / "docs.yaml").read_text(encoding="utf-8")))
