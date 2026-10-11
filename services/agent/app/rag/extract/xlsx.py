"""XLSX per sheet, with openpyxl (MIT): the first non-empty row is the header and is
repeated at the top of every chunk cut from the sheet, so a chunk of rows still says
what its columns are (ADR-0135)."""

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
                header: str | None = None
                rows: list[str] = []
                for values in sheet.iter_rows(values_only=True):
                    cells = [_cell(v) for v in values]
                    while cells and not cells[-1]:
                        cells.pop()
                    if not any(cells):
                        continue
                    line = " | ".join(cells)
                    if header is None:
                        header = line
                    else:
                        rows.append(line)
                if header is None:
                    continue
                segments.append(
                    Segment(
                        text="\n".join(rows) or header, section=sheet.title, repeat=header if rows else None
                    )
                )
        finally:
            workbook.close()
        if not segments:
            raise ExtractionFailed("empty_document")
        return ExtractedDocument(kind="xlsx", segments=segments)
