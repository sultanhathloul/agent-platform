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
    uploads_per_minute: int = Field(gt=0)  # baseline for the gateway's per-user rate limit (R3)
    # Per user in flight: bodies in the gateway (finding 2), registrations in the agent (ADR-0265).
    max_concurrent_uploads: int = Field(default=2, gt=0)
    max_concurrent_uploads_total: int = Field(default=50, gt=0)  # gateway-wide; the agent's registrations too
    # A tenant's registrations in flight (ADR-0265): none of its own by default, overrides by tenant id.
    max_concurrent_uploads_per_tenant: int | None = Field(default=None, gt=0)
    tenant_concurrent_uploads: dict[str, int] = Field(default_factory=dict)
    upload_deadline_seconds: int = Field(default=120, gt=0)  # to receive one upload's body, gateway only
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
    # The service's own memory beside its children: workers × memory_mb + this must fit the
    # container's limit, checked at startup (I7, ADR-0252).
    parent_reserve_mb: int = Field(gt=0)


class Ingest(_Strict):
    workers: int = Field(ge=1)
    embed_batch: int = Field(ge=1)
    max_attempts: int = Field(ge=1)
    sandbox: Sandbox


class Rerank(_Strict):
    """The cross-encoder step (owner decisions R2, Q1, review round 2): on or off; how many fused
    candidates it scores; and how much of each, in characters (None: the whole chunk). These come
    from the environment, not config/docs.yaml, so each deployment sets them (ADR superseding the
    ADR-0184 default); `build_documents` fills this from Settings."""

    enabled: bool = False
    candidates: int = Field(default=16, gt=0)
    max_chars: int | None = Field(default=1070, gt=0)


class Retrieval(_Strict):
    vector_top_k: int = Field(gt=0)
    text_top_k: int = Field(gt=0)
    rrf_k: int = Field(gt=0)
    # Not in config/docs.yaml: the reranker is configured per environment through .env (finding 3).
    rerank: Rerank = Rerank()
    top_k: int = Field(gt=0)
    # A report turn's searches (ADR-0401, ADR-0403): the general one, each planned chart or table, and the
    # cap on the turn's passages with an edit's re-fetched ones (ADR-0402); the search tools allow 20.
    report_top_k: int = Field(default=12, gt=0, le=20)
    report_section_k: int = Field(default=4, gt=0, le=20)
    report_max_passages: int = Field(default=40, gt=0, le=80)
    stopwords: dict[str, list[str]] = {}
    # Knowledge bases (phase 08, D3, ADR-0335): how many chunks of an older version of a source stay
    # beside its latest version when both are found, so the answer may note the change.
    superseded_keep: int = Field(default=1, ge=0, le=4)
    # Cross-KB retrieval (phase 08, I4): with several collections searched at once, each keeps at least
    # this many of its best chunks in the top_k, so one question over two KBs reaches both.
    min_per_collection: int = Field(default=0, ge=0, le=8)


class OrphanSweeper(_Strict):
    min_age_minutes: float = Field(gt=0)
    interval_minutes: float = Field(gt=0)


class DocsConfig(_Strict):
    # Which DocumentBackend serves documents (C6, I11). The core reads it as the capability's own
    # configuration (config/<slug>.yaml `backend`, `<SLUG>_BACKEND` overriding it, ADR-0235).
    backend: Literal["internal", "mcp"]
    # The server of config/mcp.yaml the `mcp` backend calls (I11); none: no `mcp` backend.
    mcp_server: str | None = None
    # One bound, in seconds, on reading all of a conversation's attachment statuses at once; past
    # it, or when a backend cannot answer, the record's last known status is shown (ADR-0233).
    status_timeout_s: float = Field(default=2.0, gt=0, le=30)
    # How long an upload waits for the backend to take the file before it answers `pending` and the
    # registration goes on in the background (I5, ADR-0254); under the gateway's 60 s for the call.
    register_response_s: float = Field(default=20.0, gt=0, le=55)
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
