"""XLSX per sheet, with openpyxl (MIT). Every chunk cut from a sheet opens with what says what its
rows are (ADR-0135, ADR-0410): the sheet's name, then the table's preamble, every non-empty row above
the first data row (two cells or more, one of them a number that is not a year): the workbook's title, the table's own title,
its number and all of its header rows, so a statistical table's continuation chunk still names its
table and «الذكور | الاناث» columns. A sheet with no data row (an index, a note) keeps the rule before
it (owner decision A3): the first row followed by a row of the same width, else the first row.
The archive is checked first and openpyxl parses through defusedxml (owner decision R4)."""

import io
import os
import re
import unicodedata
import zipfile

os.environ["OPENPYXL_DEFUSEDXML"] = "True"  # read before openpyxl is imported (owner decision R4)

import openpyxl.xml  # noqa: E402
from defusedxml import DefusedXmlException  # noqa: E402
from openpyxl import load_workbook  # noqa: E402
from openpyxl.utils.exceptions import InvalidFileException  # noqa: E402

if not openpyxl.xml.DEFUSEDXML:  # fail at import, never parse a workbook without it
    raise RuntimeError("openpyxl must parse through defusedxml (owner decision R4)")

from app.rag.config import DocsConfig
from app.rag.extract.archive import check_archive
from app.rag.extract.base import ExtractionFailed
from app.rag.types import ExtractedDocument, Segment


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return unicodedata.normalize("NFKC", str(value)).strip()


# Bumped when the text a sheet gives changes, so the seed re-indexes what an older version cut (ADR-0410).
VERSION = "2"
_NUMBER = re.compile(r"^-?\d+(?:[.,]\d+)?%?$")


_YEAR = re.compile(r"^(19|20|21)\d{2}$")


def first_data_row(rows: list[list[str]]) -> int | None:
    """The first row of two cells or more with a numeric cell that is not a year (a header row of years,
    «2025 | 2026», is still a header), or None."""
    for index, cells in enumerate(rows):
        values = [c.replace(" ", "") for c in cells if c]
        if len(values) >= 2 and any(_NUMBER.match(v) and not _YEAR.match(v) for v in values):
            return index
    return None


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
        check_archive(data, config.archive)
        try:
            workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except DefusedXmlException as exc:
            raise ExtractionFailed("xml_unsafe", detail=type(exc).__name__) from exc
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
                lines = [" | ".join(cells) for cells in rows]
                start = first_data_row(rows)
                if start:
                    # The sheet's name and the table's preamble (titles and every header row) open each chunk.
                    preamble = [line for line in lines[:start] if line.strip(" |")]
                    header, data = "\n".join([f"[{sheet.title}]", *preamble]), lines[start:]
                else:
                    at = header_index(rows)
                    header, data = f"[{sheet.title}]\n{lines[at]}", lines[:at] + lines[at + 1 :]
                segments.append(
                    Segment(text="\n".join(data) or header, section=sheet.title, repeat=header if data else None)
                )
        except DefusedXmlException as exc:  # a sheet is parsed as it is read
            raise ExtractionFailed("xml_unsafe", detail=type(exc).__name__) from exc
        finally:
            workbook.close()
        if not segments:
            raise ExtractionFailed("empty_document")
        return ExtractedDocument(kind="xlsx", segments=segments)
