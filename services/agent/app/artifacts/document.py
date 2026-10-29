"""The report page's frame and what the platform adds around a rendered spec (reports PR, ADR-0380 to
ADR-0382; option C, ADR-0406).

The platform writes the frame: `<!doctype html>`, `<html lang dir>` from the turn's locale (Arabic:
dir="rtl"), the charset and viewport and the kit marker. The kit is never stored in the page:
`KIT_MARKER` stands where it goes, and `served` puts the kit's links there for the sandboxed view or
inlines the whole kit for a download that opens offline (I6). JSON travels in `<script type="application/json">`
blocks whose `<` is escaped, so no string can close them."""

import html
import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.artifacts.config import Kit, ModelInfoText

KIT_MARKER = "<!--report-kit-->"

# The download's own policy, as the view's headers (I4): no network, the kit's scripts only by hash.
DOWNLOAD_CSP = (
    "default-src 'none'; script-src {hashes}; style-src 'unsafe-inline'; font-src data:; img-src data:; "
    "connect-src 'none'; form-action 'none'; base-uri 'none'"
)


def direction(locale: str) -> tuple[str, str]:
    return ("en", "ltr") if locale == "en" else ("ar", "rtl")


def prologue(locale: str) -> str:
    lang, dir_ = direction(locale)
    return (
        f'<!doctype html><html lang="{lang}" dir="{dir_}"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">{KIT_MARKER}'
    )


def json_block(raw_json: str, block_id: str) -> str:
    """A chart's data (`c<n>`) or the checked spec (`report-spec`), as inert JSON the kit reads."""
    kind = "kit-chart-data" if block_id != "report-spec" else "kit-spec"
    return (f'<script type="application/json" class="{kind}" id="{html.escape(block_id)}">'
            + raw_json.replace("<", "\\u003c") + "</script>")


@dataclass(frozen=True)
class ModelCall:
    """One model call behind a report, as its model strip shows it (owner decision C, ADR-0391)."""

    label: str  # what it did: wrote the report, or produced a capability's findings
    served_model: str | None  # LiteLLM's name for the deployment, "<provider>/<model id>"
    alias: str
    duration_s: float
    tokens_in: int
    tokens_out: int

    @property
    def provider_and_model(self) -> tuple[str | None, str | None]:
        if not self.served_model:
            return None, None
        provider, _, model = self.served_model.partition("/")
        return (provider, model) if model else (None, provider)


def model_strip(calls: Sequence[ModelCall], locale: str, text: ModelInfoText) -> str:
    """The strip at the end of a development report: one row per call, platform-written, never labelled."""
    lang, dir_ = direction(locale)
    unknown = text.unknown.of(locale)
    head = "".join(f"<th>{html.escape(t.of(locale))}</th>" for t in (text.call, text.model, text.provider, text.tier, text.time, text.tokens))
    rows = []
    for call in calls:
        provider, model = call.provider_and_model
        cells = (
            call.label,
            model or unknown,
            provider or unknown,
            call.alias or unknown,
            text.seconds.of(locale).format(value=f"{call.duration_s:.1f}"),
            f"{call.tokens_in:,} / {call.tokens_out:,}",
        )
        rows.append("<tr>" + "".join(f"<td><bdi>{html.escape(c)}</bdi></td>" for c in cells) + "</tr>")
    return (
        f'<footer class="kit-model-info" data-kit="model-info" lang="{lang}" dir="{dir_}">'
        f"<p>{html.escape(text.heading.of(locale))}</p>"
        f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table></footer>"
    )


_MODEL_INFO = re.compile(r'<footer class="kit-model-info" data-kit="model-info"[^>]*>.*?</footer>', re.S)


def without_model_info(page: str) -> str:
    """A stored page with its model strip removed: how it leaves when the setting is false (ADR-0391)."""
    return _MODEL_INFO.sub("", page)


def served(page: str, kit: Kit, *, form: str, kit_base: str = "") -> str:
    """The stored page as it leaves: `view` links the kit from `kit_base` (the kit path on the page's own
    origin); `download` inlines it, with a CSP of its own, so the file opens offline and loads nothing."""
    if form == "view":
        tags = (
            f'<link rel="stylesheet" href="{html.escape(kit_base + kit.config.stylesheet)}">'
            + "".join(f'<script src="{html.escape(kit_base + path)}" defer></script>' for path, _ in kit.scripts)
        )
    else:
        csp = DOWNLOAD_CSP.format(hashes=" ".join(kit.script_hashes()))
        tags = (
            f'<meta http-equiv="Content-Security-Policy" content="{csp}">'
            f"<style>{kit.inline_stylesheet()}</style>"
            + "".join(f"<script>{source}</script>" for _, source in kit.scripts)
        )
    return page.replace(KIT_MARKER, tags, 1)
