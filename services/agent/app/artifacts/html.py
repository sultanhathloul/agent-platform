"""The report's HTML, parsed incrementally and rebuilt from an allowlist (reports PR, I4, ADR-0382).

The model's output is input from outside the process (CLAUDE.md rule 12d): it is size-capped before it
is parsed (report.yaml max_model_chars) and parsed once, linearly, by the standard library's
HTMLParser as it streams. Nothing the model wrote is passed through as markup: each start tag is
re-serialized from the allowed elements and attributes below, every text is escaped, and what is
not allowed is dropped (with its content for scripts, frames, objects and templates). So the page
holds no script but the one `report-data` JSON block, no event attribute, no URL other than an
in-page anchor or a data: image, and no external stylesheet, font or import; the kit is added by the
platform when the page is served (ADR-0382).

The parser yields pieces: `Markup` (rebuilt markup), `Text` (visible text, kept apart so the guards and
the figure check see it), `Data` (the report-data block's JSON text) and `Style` (a <style> block's
filtered CSS). `Text.where` says where the text is: `title` (the document title), `svg` (inside an
<svg>, where a span cannot wrap a figure) or `body`."""

import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

# Elements kept, with their children.
HTML_ELEMENTS = frozenset(
    ["html", "head", "body", "title", "meta", "main", "header", "footer", "section", "article", "aside", "nav", "div", "span", "p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "dl", "dt", "dd", "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "colgroup", "col", "strong", "b", "em", "i", "u", "s", "small", "mark", "sub", "sup", "br", "hr", "blockquote", "q", "cite", "figure", "figcaption", "details", "summary", "bdi", "bdo", "abbr", "time", "code", "pre", "kbd", "a", "img", "progress", "meter", "wbr"]
)
SVG_ELEMENTS = frozenset(
    ["svg", "g", "path", "circle", "ellipse", "rect", "line", "polyline", "polygon", "text", "tspan", "defs", "lineargradient", "radialgradient", "stop", "desc", "clippath", "symbol", "marker", "pattern"]
)
VOID = frozenset({"meta", "br", "hr", "img", "col", "wbr"})
# Every element HTML gives no end tag: a dropped one never opens a skip.
HTML_VOID = VOID | frozenset({"area", "base", "embed", "input", "link", "param", "source", "track", "keygen"})
# Dropped with everything inside them.
DROP_WITH_CONTENT = frozenset(
    ["script", "iframe", "frame", "frameset", "object", "embed", "applet", "template", "noscript", "noembed", "noframes", "audio", "video", "canvas", "math", "foreignobject", "use", "image", "animate", "animatemotion", "animatetransform", "set", "select", "textarea", "option", "optgroup", "datalist", "base", "link"]
)
# The document's frame: written by the platform (app/artifacts/document.py), never copied from the model.
FRAME = frozenset({"html", "head", "body"})

GLOBAL_ATTRIBUTES = frozenset({"class", "id", "dir", "lang", "title", "role", "style", "hidden", "tabindex"})
ELEMENT_ATTRIBUTES = {
    "td": {"colspan", "rowspan", "headers", "scope"},
    "th": {"colspan", "rowspan", "headers", "scope", "abbr"},
    "col": {"span"},
    "colgroup": {"span"},
    "details": {"open"},
    "time": {"datetime"},
    "ol": {"start", "reversed", "type"},
    "li": {"value"},
    "progress": {"value", "max"},
    "meter": {"value", "min", "max", "low", "high", "optimum"},
    "img": {"src", "alt", "width", "height", "loading"},
    "a": {"href"},
    "meta": {"charset", "name", "content"},
    "html": {"xmlns"},
    "abbr": set(),
    "bdo": set(),
}
SVG_ATTRIBUTES = frozenset(
    ["viewbox", "xmlns", "width", "height", "d", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry", "fx", "fy", "points", "fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "stroke-dasharray", "stroke-dashoffset", "stroke-miterlimit", "opacity", "fill-opacity", "stroke-opacity", "fill-rule", "clip-rule", "transform", "offset", "stop-color", "stop-opacity", "gradientunits", "gradienttransform", "spreadmethod", "font-size", "font-weight", "font-style", "text-anchor", "dominant-baseline", "alignment-baseline", "letter-spacing", "preserveaspectratio", "clip-path", "marker-start", "marker-mid", "marker-end", "markerwidth", "markerheight", "refx", "refy", "orient", "patternunits", "patterncontentunits", "pathlength", "dx", "dy", "rotate", "textlength", "lengthadjust", "vector-effect", "shape-rendering", "paint-order"]
)
PLATFORM_CLASSES = frozenset(
    {"kit-figure", "kit-figure-svg", "kit-figure-note", "kit-sources", "kit-sources-note", "kit-theme-toggle", "kit-model-info"}
)
# The attributes of the platform's marks (a figure's label, ADR-0388): never the model's.
PLATFORM_ATTRIBUTES = frozenset({"data-kit", "data-kit-source", "data-kit-label"})
# Attribute values a person reads: checked by the guards with the text (I4).
VISIBLE_ATTRIBUTES = ("title", "alt", "aria-label", "data-label")

_DATA_IMAGE = re.compile(r"^\s*data:image/(png|jpeg|gif|webp);base64,[a-z0-9+/=\s]+$", re.I)
_URL = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.I | re.S)
_IMPORT = re.compile(r"@import[^;{]*;?|@font-face\s*\{[^}]*\}|@namespace[^;]*;?", re.I)
_UNSAFE_CSS = re.compile(r"expression\s*\(|javascript\s*:|vbscript\s*:|behavio(u)?r\s*:|-moz-binding|</", re.I)
_REF_ONLY = re.compile(r"^url\(#[A-Za-z][\w\-:.]*\)$")


def clean_css(css: str) -> str:
    """CSS with every url() that is not an in-document #reference removed, and no import, font-face,
    script scheme or `</`: a page loads nothing a stylesheet names (I4)."""
    css = _IMPORT.sub("", css)

    def url(match: re.Match) -> str:
        target = match.group(2).strip()
        return match.group(0) if target.startswith("#") else "none"

    css = _URL.sub(url, css)
    return _UNSAFE_CSS.sub("", css)


def _attribute(tag: str, name: str, value: str, in_svg: bool) -> str | None:
    """The attribute's kept value, or None to drop it."""
    if name.startswith("on") or name in ("srcdoc", "formaction", "srcset", "ping", "background", "xlink:href"):
        return None
    if name == "style":
        return clean_css(value)
    if name == "class":
        # The platform's own marks are never the model's: a figure's source label, the sources section.
        return " ".join(c for c in value.split() if c not in PLATFORM_CLASSES)
    if name in PLATFORM_ATTRIBUTES:
        return None
    if name.startswith("aria-") or name.startswith("data-"):
        return value if re.fullmatch(r"[a-z][a-z0-9\-]*", name) else None
    if tag == "a" and name == "href":
        return value if re.fullmatch(r"#[\w\-:.]*", value.strip()) else None
    if tag == "img" and name == "src":
        return value if _DATA_IMAGE.match(value) else None
    if tag == "meta":
        if name == "charset":
            return "utf-8"
        if name == "name":
            return value if value.lower() == "viewport" else None
        if name == "content":
            return value if re.fullmatch(r"[\w\s=.,\-]*", value) else None
    allowed = GLOBAL_ATTRIBUTES | ELEMENT_ATTRIBUTES.get(tag, set())
    if in_svg and (name in SVG_ATTRIBUTES or tag in SVG_ELEMENTS and name in GLOBAL_ATTRIBUTES):
        if "url(" in value.lower():
            return value if _REF_ONLY.match(value.strip()) else None
        return value
    return value if name in allowed else None


@dataclass
class Markup:
    html: str
    # A start tag's visible attribute values (VISIBLE_ATTRIBUTES), checked by the guards with the text;
    # `html` is rebuilt from `attrs` after a check changed one.
    tag: str = ""
    attrs: list[tuple[str, str]] = field(default_factory=list)
    end: bool = False
    void: bool = False


@dataclass
class Text:
    text: str
    where: str = "body"  # body | title | svg


@dataclass
class Data:
    raw: str
    parsed: dict | None = None  # the block as the kit reads it (app/artifacts/document.py normalize_data)


@dataclass
class Style:
    css: str


Piece = Markup | Text | Data | Style


def start_tag(tag: str, attrs: list[tuple[str, str]], void: bool = False) -> str:
    rendered = "".join(f' {name}="{html.escape(value, quote=True)}"' for name, value in attrs)
    return f"<{tag}{rendered}>"


# Text before the page begins, held while waiting for `<!doctype` or `<html`; past it, the first tag begins it.
LEAD_LIMIT = 4000
_END_TAG = "</html"


class Sanitizer(HTMLParser):
    """Feed the model's text as it streams; `drain()` returns the pieces complete so far."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._pieces: list[Piece] = []
        self._open: list[str] = []  # elements kept and still open, innermost last
        self._skip: list[str] = []  # dropped elements whose content is dropped too
        self._raw: str | None = None  # script (report-data) or style while its content arrives
        self._raw_kind = ""
        self._data_seen = False
        self.doctype = False
        self.html_attrs: dict[str, str] = {}
        self._text: Text | None = None
        self._begun = False
        self._ended = False
        self._lead = ""
        self._carry = ""

    # ---------------------------------------------------------------- the stream
    def feed_model(self, delta: str) -> None:
        """The model's text: anything before the document (a sentence, a Markdown fence) and after its
        `</html>` (a closing fence) is not the page and is never parsed."""
        if self._ended:
            return
        if not self._begun:
            self._lead += delta
            lowered = self._lead.lower()
            at = min((i for i in (lowered.find("<!doctype"), lowered.find("<html")) if i >= 0), default=-1)
            if at < 0 and len(self._lead) > LEAD_LIMIT:
                at = lowered.find("<")
            if at < 0:
                return
            self._begun, delta, self._lead = True, self._lead[at:], ""
        # The last few characters wait for the next chunk: "</html" may arrive split in two.
        pending, self._carry = self._carry + delta, ""
        end = pending.lower().find("</html")
        if end >= 0:
            self._ended = True
            self.feed(pending[:end])
            return
        keep = len(_END_TAG) - 1
        self._carry = pending[-keep:] if len(pending) > keep else pending
        self.feed(pending[: len(pending) - len(self._carry)])

    def drain(self) -> list[Piece]:
        pieces, self._pieces = self._pieces, []
        merged: list[Piece] = []
        for piece in pieces:
            if isinstance(piece, Text) and merged and isinstance(merged[-1], Text) and merged[-1].where == piece.where:
                merged[-1] = Text(merged[-1].text + piece.text, piece.where)
            else:
                merged.append(piece)
        return merged

    def finish(self) -> list[Piece]:
        """Ends the stream: text left in the parser's buffer, then every element still open closed."""
        if not self._begun and self._lead:
            at = self._lead.find("<")
            if at >= 0:
                self.feed(self._lead[at:])
        if self._carry and not self._ended:
            self.feed(self._carry)
        self._carry = ""
        self.close()
        self._flush_text()
        if self._raw is not None:  # a block the model never closed is dropped
            self._raw = None
        while self._open:
            tag = self._open.pop()
            if tag not in VOID and tag not in FRAME:
                self._pieces.append(Markup(f"</{tag}>", tag=tag, end=True))
        return self.drain()

    @property
    def in_svg(self) -> bool:
        return "svg" in self._open

    # ---------------------------------------------------------------- HTMLParser
    def handle_decl(self, decl: str) -> None:
        self._flush_text()
        if decl.lower().startswith("doctype"):
            self.doctype = True

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, closed=False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, closed=True)

    def _start(self, tag: str, attrs: list[tuple[str, str | None]], *, closed: bool) -> None:
        self._flush_text()
        if self._skip:
            if tag == self._skip[-1] and not closed and tag not in HTML_VOID:
                self._skip.append(tag)
            return
        values = [(name.lower(), "" if value is None else value) for name, value in attrs]
        if tag == "script":
            kind = dict(values).get("type", "").strip().lower()
            if kind == "application/json" and dict(values).get("id") == "report-data" and not self._data_seen:
                self._data_seen = True
                self._raw, self._raw_kind = "", "data"
            else:
                self._raw, self._raw_kind = "", "drop"
            return
        if tag == "style":
            self._raw, self._raw_kind = "", "style"
            return
        if tag in DROP_WITH_CONTENT:
            if not closed and tag not in HTML_VOID:
                self._skip.append(tag)
            return
        in_svg = self.in_svg or tag == "svg"
        if tag not in HTML_ELEMENTS and not (in_svg and tag in SVG_ELEMENTS):
            return  # an unknown or unsafe tag: dropped, its text kept
        kept = [(name, cleaned) for name, value in values if (cleaned := _attribute(tag, name, value, in_svg)) is not None]
        if tag in FRAME:
            if tag == "html" and not self.html_attrs:
                self.html_attrs = dict(kept)
            if tag not in self._open:
                self._open.append(tag)
            return
        void = tag in VOID or (closed and in_svg)
        self._pieces.append(Markup(start_tag(tag, kept), tag=tag, attrs=kept, void=void))
        if void:
            if closed and in_svg and tag not in VOID:
                self._pieces.append(Markup(f"</{tag}>", tag=tag, end=True))
            return
        self._open.append(tag)

    def handle_endtag(self, tag: str) -> None:
        self._flush_text()
        if self._raw is not None:
            if tag in ("script", "style"):
                self._close_raw()
            return
        if self._skip:
            if tag == self._skip[-1]:
                self._skip.pop()
            return
        if tag not in self._open:
            return  # never opened, or dropped: ignored
        while self._open:
            current = self._open.pop()
            if current not in FRAME:
                self._pieces.append(Markup(f"</{current}>", tag=current, end=True))
            if current == tag:
                break

    def handle_data(self, data: str) -> None:
        if self._raw is not None:
            self._raw += data
            return
        if self._skip or not data:
            return
        # Held until the next tag: the parser hands text over as it arrives, and a figure split between two
        # chunks must be checked whole.
        where = "title" if "title" in self._open and not self.in_svg else ("svg" if self.in_svg else "body")
        if self._text is not None and self._text.where != where:
            self._flush_text()
        self._text = Text((self._text.text if self._text else "") + data, where)

    def _flush_text(self) -> None:
        if self._text is not None:
            self._pieces.append(self._text)
            self._text = None

    def handle_comment(self, data: str) -> None:
        self._flush_text()
        return  # comments are dropped

    def handle_pi(self, data: str) -> None:
        return

    def unknown_decl(self, data: str) -> None:
        return

    def _close_raw(self) -> None:
        raw, kind = self._raw or "", self._raw_kind
        self._raw = None
        if kind == "data":
            self._pieces.append(Data(raw))
        elif kind == "style":
            self._pieces.append(Style(clean_css(raw)))


def render_text(text: str) -> str:
    return html.escape(text, quote=False)


def render_data(raw_json: str) -> str:
    """The report-data block; `<` is escaped inside the JSON, so no string can close the script."""
    return '<script type="application/json" id="report-data">' + raw_json.replace("<", "\\u003c") + "</script>"


def render_style(css: str) -> str:
    return "<style>" + css.replace("<", "\\3c ") + "</style>"
