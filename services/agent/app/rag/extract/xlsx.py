"""XLSX per sheet, with openpyxl (MIT). The header is the first row followed by a data row of
the same width (owner decision A3): a title or a note above the table («ميزانية عام 2031»), one
cell wide, is not taken for the header. The header is repeated at the top of every chunk cut
from the sheet, so a chunk of rows still says what its columns are (ADR-0135); rows above it
stay in the sheet's text, before the table. A sheet with no such pair takes its first row."""

import io
import unicodedata
import zipfile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from app.rag.config import DocsConfig
from app.rag.extract.base import ExtractionFailed
from app.rag.types import ExtractedDocument, Segment


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return unicodedata.normalize("NFKC", str(value)).strip()


def header_index(rows: list[list[str]]) -> int:
    """The first row followed by a row of the same width (two cells or more), else the first."""
    for index in range(len(rows) - 1):
        width = len(rows[index])
        if width >= 2 and len(rows[index + 1]) == width:
            return index
    return 0


class XlsxExtractor:
    kind = "xlsx"

    def extract(self, data: bytes, config: DocsConfig) -> ExtractedDocument:
        try:
            workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except (InvalidFileException, zipfile.BadZipFile, KeyError, ValueError) as exc:
            raise ExtractionFailed("corrupt_file", detail=type(exc).__name__) from exc
        segments: list[Segment] = []
        try:
            for sheet in workbook.worksheets:
                rows: list[list[str]] = []
                for values in sheet.iter_rows(values_only=True):
                    cells = [_cell(v) for v in values]
                    while cells and not cells[-1]:
                        cells.pop()
                    if any(cells):
                        rows.append(cells)
                if not rows:
                    continue
                at = header_index(rows)
                lines = [" | ".join(cells) for cells in rows]
                header, data = lines[at], lines[:at] + lines[at + 1 :]
                segments.append(
                    Segment(
                        text="\n".join(data) or header, section=sheet.title, repeat=header if data else None
                    )
                )
        finally:
            workbook.close()
        if not segments:
            raise ExtractionFailed("empty_document")
        return ExtractedDocument(kind="xlsx", segments=segments)
