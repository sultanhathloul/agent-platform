"""Upload limits (ADR-0126), from the `limits` section of config/docs.yaml, the one file the
gateway reads too (owner decision R3). A generic type: the attachment service needs the file
count, the allowed kinds by extension and the uploads in flight, and nothing else of the document
configuration.

Uploads in flight (the PR #12 review, decision 4, ADR-0265): a registration counts toward its
user's `max_concurrent_uploads` until it ends, answered or still going on in the background; a
tenant's share is optional (`max_concurrent_uploads_per_tenant`, none by default, and
`tenant_concurrent_uploads` by tenant id), and `max_concurrent_uploads_total` bounds them all."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.resilience.limits import Caps, ConcurrencyLimiter


class _FileType(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extensions: list[str]
    mimes: list[str]


class UploadLimits(BaseModel):
    model_config = ConfigDict(extra="ignore")

    max_file_bytes: int = Field(gt=0)
    max_files_per_conversation: int = Field(gt=0)
    max_pdf_pages: int = Field(gt=0)
    types: dict[str, _FileType]
    max_concurrent_uploads: int = Field(default=2, ge=1)
    max_concurrent_uploads_total: int = Field(default=50, ge=1)
    max_concurrent_uploads_per_tenant: int | None = Field(default=None, ge=1)
    tenant_concurrent_uploads: dict[str, int] = Field(default_factory=dict)

    def registrations(self) -> ConcurrencyLimiter:
        """The registrations in flight, per user, per tenant and in all (ADR-0265)."""
        return ConcurrencyLimiter(
            Caps(
                per_user=self.max_concurrent_uploads,
                per_tenant=self.max_concurrent_uploads_per_tenant,
                tenant_overrides=dict(self.tenant_concurrent_uploads),
                platform=self.max_concurrent_uploads_total,
            )
        )

    def kind_of(self, name: str) -> str | None:
        suffix = Path(name).suffix.lower().lstrip(".")
        for kind, spec in self.types.items():
            if suffix in spec.extensions:
                return kind
        return None


def load_upload_limits(config_dir: Path) -> UploadLimits:
    raw = yaml.safe_load((config_dir / "docs.yaml").read_text(encoding="utf-8"))
    return UploadLimits.model_validate(raw["limits"])
