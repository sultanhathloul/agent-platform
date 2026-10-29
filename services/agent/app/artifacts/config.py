"""config/artifacts/ (reports PR, ADR-0364 onward): report.yaml, the design kit's prompt part
(design-system.md, I2) and the kit's files, loaded and checked at startup. A missing kit file, a
design-system.md without its front-matter, or a keyword list that is empty fails startup."""

import base64
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.S)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Localized(_Strict):
    ar: str
    en: str

    def of(self, locale: str) -> str:
        return self.en if locale == "en" else self.ar


class Keywords(_Strict):
    ar: list[str] = Field(min_length=1)
    en: list[str] = Field(min_length=1)

    def all(self) -> list[str]:
        return [*self.ar, *self.en]


class StreamConfig(_Strict):
    flush_chars: int = Field(gt=0)


class StorageConfig(_Strict):
    bucket: str = Field(min_length=3)
    store_s: float = Field(gt=0)


class KitConfig(_Strict):
    version: str = Field(pattern=r"^v\d+$")
    path: str = Field(pattern=r"^/report-kit/v\d+/$")
    stylesheet: str
    scripts: list[str] = Field(min_length=1)
    fonts: list[str] = Field(min_length=1)


class ModelInfoText(_Strict):
    heading: Localized
    wrote: Localized
    findings: Localized
    call: Localized
    model: Localized
    provider: Localized
    tier: Localized
    time: Localized
    tokens: Localized
    seconds: Localized
    unknown: Localized


class ShowModelInfo(_Strict):
    """Owner decision C (ADR-0391): whether a report shows its model strip, per deployment profile."""

    development: bool
    production: bool


class ElementKinds(_Strict):
    kpi_row: Localized
    chart: Localized
    table: Localized


class ReportText(_Strict):
    sources_heading: Localized
    no_sources: Localized
    general_banner: Localized
    kinds: ElementKinds
    untitled_element: Localized
    sheet: Localized
    unavailable: Localized
    change: Localized
    chart_gaps: Localized
    page: Localized
    created: Localized
    updated: Localized
    untitled: Localized
    partial: Localized
    missing_elements: Localized
    trimmed_categories: Localized
    gaps: Localized
    nothing_found: Localized
    model_info: ModelInfoText


class ReportConfig(_Strict):
    version: str
    keywords: Keywords
    max_bytes: int = Field(gt=0)
    max_model_chars: int = Field(gt=0)
    current_max_tokens: int = Field(gt=0)
    stream: StreamConfig
    storage: StorageConfig
    show_model_info: ShowModelInfo
    kit: KitConfig
    text: ReportText


@dataclass(frozen=True)
class DesignSystem:
    """design-system.md: reviewed text, rendered as the report writer's `report_kit` block."""

    id: str
    version: str
    text: str

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"


@dataclass(frozen=True)
class Kit:
    """The kit's files, read once: served as they are by the gateway; inlined into a download."""

    config: KitConfig
    stylesheet: str
    scripts: tuple[tuple[str, str], ...]  # (path, source) in load order
    fonts: dict[str, bytes]

    def inline_stylesheet(self) -> str:
        """kit.css with each font as a data: URL, for a download that opens offline."""
        css = self.stylesheet
        for path, data in self.fonts.items():
            url = "data:font/woff2;base64," + base64.b64encode(data).decode("ascii")
            css = css.replace(f"url({path})", f"url({url})")
        return css

    def script_hashes(self) -> list[str]:
        """CSP hash sources of the inlined scripts, so a download runs them and nothing else."""
        return [
            "'sha256-" + base64.b64encode(hashlib.sha256(source.encode("utf-8")).digest()).decode("ascii") + "'"
            for _, source in self.scripts
        ]


@dataclass(frozen=True)
class Artifacts:
    report: ReportConfig
    design: DesignSystem
    kit: Kit
    # report.yaml's show_model_info for this deployment's profile (ADR-0391).
    show_model_info: bool = False


def _design(path: Path) -> DesignSystem:
    match = _FRONT_MATTER.match(path.read_text(encoding="utf-8"))
    if not match:
        raise ValueError(f"{path}: missing YAML front-matter")
    meta = yaml.safe_load(match.group(1)) or {}
    if not meta.get("id") or not meta.get("version"):
        raise ValueError(f"{path}: front-matter needs id and version")
    return DesignSystem(str(meta["id"]), str(meta["version"]), match.group(2).strip())


def _kit(root: Path, config: KitConfig) -> Kit:
    def read(name: str) -> Path:
        path = (root / name).resolve()
        if root.resolve() not in path.parents or not path.is_file():
            raise ValueError(f"kit file {name} is missing from {root}")
        return path

    stylesheet = read(config.stylesheet).read_text(encoding="utf-8")
    fonts = {name: read(name).read_bytes() for name in config.fonts}
    for name in fonts:
        if f"url({name})" not in stylesheet:
            raise ValueError(f"kit.css does not use the font {name}")
    scripts = tuple((name, read(name).read_text(encoding="utf-8")) for name in config.scripts)
    return Kit(config=config, stylesheet=stylesheet, scripts=scripts, fonts=fonts)


def load_artifacts(config_dir: Path, profile: str = "production") -> Artifacts:
    """`profile`: the deployment's (Settings.platform_profile); anything but development is production."""
    folder = config_dir / "artifacts"
    report = ReportConfig.model_validate(yaml.safe_load((folder / "report.yaml").read_text(encoding="utf-8")))
    show = report.show_model_info.development if profile == "development" else report.show_model_info.production
    return Artifacts(
        report=report, design=_design(folder / "design-system.md"), kit=_kit(folder / "kit", report.kit), show_model_info=show
    )
