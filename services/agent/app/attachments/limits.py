"""Upload limits (ADR-0126), from the `limits` section of config/docs.yaml, the one file the
gateway reads too (owner decision R3). A generic type: the attachment service needs the file
count and the allowed kinds by extension, and nothing else of the document configuration."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


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

    def kind_of(self, name: str) -> str | None:
        suffix = Path(name).suffix.lower().lstrip(".")
        for kind, spec in self.types.items():
            if suffix in spec.extensions:
                return kind
        return None


def load_upload_limits(config_dir: Path) -> UploadLimits:
    raw = yaml.safe_load((config_dir / "docs.yaml").read_text(encoding="utf-8"))
    return UploadLimits.model_validate(raw["limits"])
