"""Why a settings save was refused, in plain words (config/messages/settings.yaml, ADR-0278).

A rule's name gives its family (before the first colon) and its subject (after it): a capability,
a tool, an MCP server, a document service or a setting's key. `describe` renders the rule's
sentence in Arabic and English with the capability's own name and the tool's own label, never a
technical id inside an Arabic sentence; the ids travel in `params` for the page's chips."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.resilience.budget import Check

# A generated rule's subject, by family: the names of the parts after the family.
_PARTS: dict[str, tuple[str, ...]] = {
    "capability-in-room": ("capability",),
    "tool-in-capability": ("capability", "tool"),
    "facade-search-in-tool": ("capability", "tool"),
    "document-server-in-tool": ("capability", "tool"),
    "slot-and-attempt-in-broker": ("server",),
    "sandbox-memory": ("service",),
    "range": ("key",),
    "unknown": ("key",),
}

Names = Callable[[str, str], tuple[str, str]]  # (kind, id) -> (ar, en)


def family(name: str) -> str:
    return name.split(":", 1)[0]


def params(name: str) -> dict[str, str]:
    """The ids a rule names: {"capability": "docs", "tool": "docs_search"} and so on."""
    parts = name.split(":")[1:]
    keys = _PARTS.get(family(name), ())
    return dict(zip(keys, parts, strict=False))


_ARABIC_DIGITS = str.maketrans("0123456789.", "٠١٢٣٤٥٦٧٨٩٫")
_NUMBERS = ("inner", "outer", "value", "maximum", "minimum")


def _number(value: float | None) -> str:
    return "" if value is None else f"{value:g}"


@dataclass(frozen=True)
class SettingsMessages:
    rules: dict[str, dict[str, str]]
    families: dict[str, dict[str, str]]
    ranges: dict[str, dict[str, str]]
    fields: dict[str, dict[str, str]]

    @classmethod
    def load(cls, config_dir: Path) -> "SettingsMessages":
        raw = yaml.safe_load((config_dir / "messages" / "settings.yaml").read_text(encoding="utf-8"))
        loaded = cls(raw["rules"], raw["families"], raw["ranges"], raw["fields"])
        for group in (loaded.rules, loaded.families, loaded.ranges, loaded.fields):
            for key, text in group.items():
                if not text.get("ar") or not text.get("en"):
                    raise ValueError(f"config/messages/settings.yaml: {key} needs both ar and en")
        return loaded

    def field(self, key: str) -> dict[str, str]:
        """A setting's label; a server's max_concurrency shares one label (the server is a chip)."""
        if key.startswith("mcp.") and key.endswith(".max_concurrency"):
            key = "mcp.max_concurrency"
        return self.fields.get(key, {"ar": "إعداد", "en": key})

    def template(self, check: Check) -> dict[str, str]:
        kind = family(check.name)
        if kind in ("range", "unknown"):
            return self.ranges.get(check.row, self.ranges["unknown"])
        return self.rules.get(check.name) or self.families.get(kind) or self.families["missing"]

    def describe(self, check: Check, names: Names, minimum: float | None = None) -> tuple[str, str]:
        """The rule's sentence in Arabic and in English."""
        template = self.template(check)
        found = params(check.name)
        out = []
        for locale, index in (("ar", 0), ("en", 1)):
            label = self.field(found["key"])[locale] if "key" in found else ""
            values = {
                "inner": _number(check.inner_v),
                "outer": _number(check.outer_v),
                "value": _number(check.inner_v),
                "maximum": _number(check.outer_v),
                "minimum": _number(minimum),
                "field": label,
                "capability": names("capability", found["capability"])[index]
                if "capability" in found
                else "",
                "tool": names("tool", found["tool"])[index] if "tool" in found else "",
            }
            if locale == "ar":  # the page writes numbers in Arabic-Indic digits (ar-SA); so do these
                values = {k: v.translate(_ARABIC_DIGITS) if k in _NUMBERS else v for k, v in values.items()}
            out.append(template[locale].format(**values))
        return out[0], out[1]
