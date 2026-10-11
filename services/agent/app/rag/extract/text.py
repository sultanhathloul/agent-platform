"""TXT and MD, heading-aware: a Markdown heading (`#`…`######`) or an Arabic structural
heading on its own line (الباب، الفصل، المادة، البند) starts a new section. Text is
UTF-8 (a BOM is dropped), else Windows-1256, the common Arabic legacy encoding."""

import re
import unicodedata

from app.rag.config import DocsConfig
from app.rag.extract.base import ExtractionFailed
from app.rag.types import DocumentKind, ExtractedDocument, Segment

_MD_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_AR_HEADING = re.compile(r"^\s*((?:ال)?(?:باب|فصل|مادة|بند)\b.{0,60}?)\s*[:：]?\s*$")


def decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp1256")
    except UnicodeDecodeError as exc:
        raise ExtractionFailed("corrupt_file", detail="encoding") from exc


def _heading(line: str) -> tuple[int, str] | None:
    if m := _MD_HEADING.match(line):
        return len(m.group(1)), m.group(2).strip()
    if m := _AR_HEADING.match(line):
        return 7, m.group(1).strip()  # below every Markdown level
    return None


class TextExtractor:
    def __init__(self, kind: DocumentKind):
        self.kind = kind

    def extract(self, data: bytes, config: DocsConfig) -> ExtractedDocument:
        text = unicodedata.normalize("NFKC", decode(data)).replace("\r\n", "\n").replace("\r", "\n")
        path: list[tuple[int, str]] = []
        segments: list[Segment] = []
        lines: list[str] = []

        def flush() -> None:
            body = "\n".join(lines).strip()
            if body:
                section = " › ".join(title for _, title in path) or None
                segments.append(Segment(text=body, section=section))
            lines.clear()

        for line in text.split("\n"):
            heading = _heading(line)
            if heading is not None:
                flush()
                level, title = heading
                while path and path[-1][0] >= level:
                    path.pop()
                path.append((level, title))
                lines.append(title)
            else:
                lines.append(line)
        flush()
        if not segments:
            raise ExtractionFailed("empty_document")
        return ExtractedDocument(kind=self.kind, segments=segments)
