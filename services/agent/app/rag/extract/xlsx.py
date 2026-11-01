"""XLSX per sheet, with openpyxl (MIT). Every chunk cut from a sheet opens with what says what its
rows are (ADR-0135, ADR-0410): the sheet's name, then the table's preamble, every non-empty row above
the first data row (two cells or more, one of them a number that is not a year): the workbook's title, the table's own title,
its number and all of its header rows, so a statistical table's continuation chunk still names its
table and «الذكور | الاناث» columns. A sheet with no data row (an index, a note) keeps the rule before
it (owner decision A3): the first row followed by a row of the same width, else the first row.
Version 3 (ADR-0413): a merged header cell names every column it spans (a merge as wide as the table, a
title, stays in its first cell); the preamble ends with `[columns]`, each column's full label from its
header rows («غير السعوديين › الإجمالي»); a numeric cell reads as the workbook displays it, through its
number format (83.6, not 83.60299013). Version 5 (ADR-0422): the workbook is streamed read-only, as
before version 3; the merged ranges are read from each sheet's XML in one pass and filled only within the
rows the sheet holds and the table's width, so memory stays near the sheet's text.
The archive is checked first and openpyxl parses through defusedxml (owner decision R4)."""

import bisect
import io
import os
import re
import unicodedata
import zipfile
from decimal import ROUND_HALF_UP, Decimal

os.environ["OPENPYXL_DEFUSEDXML"] = "True"  # read before openpyxl is imported (owner decision R4)

import openpyxl.xml  # noqa: E402
from defusedxml import DefusedXmlException  # noqa: E402
from defusedxml.ElementTree import iterparse  # noqa: E402
from openpyxl import load_workbook  # noqa: E402
from openpyxl.utils.cell import range_boundaries  # noqa: E402
from openpyxl.utils.exceptions import InvalidFileException  # noqa: E402

if not openpyxl.xml.DEFUSEDXML:  # fail at import, never parse a workbook without it
    raise RuntimeError("openpyxl must parse through defusedxml (owner decision R4)")

from app.rag.config import DocsConfig
from app.rag.extract.archive import check_archive
from app.rag.extract.base import ExtractionFailed
from app.rag.types import ExtractedDocument, Segment


_LOCALE = re.compile(r"\[[^\]]*\]")
_QUOTED = re.compile(r'"([^"]*)"')


def displayed(value: float | int, number_format: str | None) -> str:
    """A number as the workbook displays it (ADR-0413): its number format's decimals, thousands separator
    and percent sign; "General" as a spreadsheet shows it, at most 10 significant digits. Formats with
    dates, fractions or conditions are read as General."""
    fmt = (number_format or "General").split(";")[0]
    fmt = _LOCALE.sub("", fmt)
    literals = "".join(_QUOTED.findall(fmt))
    code = _QUOTED.sub("", fmt).replace("_)", "").replace("\\", "").replace("(", "").replace(")", "")
    if not code or code == "General" or any(ch in code for ch in "dmyhs/?@E"):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return f"{value:.10g}" if isinstance(value, float) else str(value)
    percent = "%" in code
    shown = value * 100 if percent else value
    whole, _, fraction = code.replace("%", "").partition(".")
    places = sum(1 for ch in fraction if ch in "0#")
    grouped = "," in whole
    # Half away from zero, as a spreadsheet rounds (Python's format rounds half to even).
    shown = Decimal(repr(shown)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    text = f"{shown:,.{places}f}" if grouped else f"{shown:.{places}f}"
    return text + ("%" if percent else "") + literals


def _cell(cell) -> str:
    value = cell.value if hasattr(cell, "value") else cell
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int | float):
        return displayed(value, getattr(cell, "number_format", None))
    return unicodedata.normalize("NFKC", str(value)).strip()


def merged_ranges(sheet) -> list[tuple[int, int, int, int]]:
    """A read-only sheet's merged ranges (min_col, min_row, max_col, max_row), read from its XML in one
    streaming pass through defusedxml (ADR-0422): `<mergeCell ref="B2:D2"/>` follows the sheet's data, and
    each element is cleared once read, so the pass holds no more than one element at a time."""
    ranges: list[tuple[int, int, int, int]] = []
    data = None
    with sheet.parent._archive.open(sheet._worksheet_path) as source:
        for event, element in iterparse(source, events=("start", "end")):
            if event == "start":
                if element.tag.endswith("}sheetData"):
                    data = element
                continue
            if element.tag.endswith("}row") and data is not None:
                # A row read is dropped from its parent, so the cells already read are not kept.
                element.clear()
                data.remove(element)
            elif element.tag.endswith("}mergeCell"):
                try:
                    bounds = range_boundaries(element.get("ref") or "")
                except ValueError:
                    bounds = None
                if bounds and None not in bounds:
                    ranges.append(bounds)
    return ranges


def fill_merged(grid: dict[int, list[str]], ranges: list[tuple[int, int, int, int]]) -> None:
    """A merged range's value in each cell it spans (ADR-0413): «غير السعوديين» over three columns names
    each of them. A range as wide as the table's value columns (a title, «الجنسية» over all of them) is
    left in its first cell: it names the whole table, not a column. Only rows the sheet holds and columns
    up to the table's width are filled (ADR-0422): a range declared over a million rows costs the rows
    that exist. `grid` is each row's displayed cells by row number, changed in place."""
    # The table's width: its last column that holds a value (formatting alone widens max_column).
    width = max((len(cells) for cells in grid.values()), default=1)
    numbers = sorted(grid)
    for min_col, min_row, max_col, max_row in ranges:
        if max_col - min_col + 1 >= width - 1 and min_row == max_row:
            continue
        top_row = grid.get(min_row) or []
        top = top_row[min_col - 1] if min_col - 1 < len(top_row) else ""
        if not top:
            continue
        last = min(max_col, width)
        for row in numbers[bisect.bisect_left(numbers, min_row) : bisect.bisect_right(numbers, max_row)]:
            cells = grid[row]
            for col in range(min_col, last + 1):
                if (row, col) == (min_row, min_col):
                    continue
                if col > len(cells):
                    cells.extend([""] * (col - len(cells)))
                if not cells[col - 1]:
                    cells[col - 1] = top


def column_labels(header_rows: list[list[str]]) -> list[str]:
    """Each column's full label from the header rows, top to bottom, repeats dropped: «غير السعوديين ›
    الإجمالي»."""
    width = max((len(r) for r in header_rows), default=0)
    labels = []
    for col in range(width):
        parts: list[str] = []
        for row in header_rows:
            cell = row[col] if col < len(row) else ""
            if cell and cell not in parts:
                parts.append(cell)
        labels.append(" › ".join(parts))
    return labels


# Bumped when the text a sheet gives changes, so the seed re-indexes what an older version cut (ADR-0410).
VERSION = "5"
_NUMBER = re.compile(r"^-?\d{1,3}(?:,\d{3})+(?:\.\d+)?%?$|^-?\d+(?:[.,]\d+)?%?$")


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
            # Streamed read-only (ADR-0422): a read-only cell carries its number format; merged ranges are
            # read from the sheet's XML (merged_ranges).
            workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except DefusedXmlException as exc:
            raise ExtractionFailed("xml_unsafe", detail=type(exc).__name__) from exc
        except (InvalidFileException, zipfile.BadZipFile, KeyError, ValueError) as exc:
            raise ExtractionFailed("corrupt_file", detail=type(exc).__name__) from exc
        segments: list[Segment] = []
        try:
            for sheet in workbook.worksheets:
                # The rows as the sheet holds them, not padded to a declared dimension (ADR-0422).
                sheet.reset_dimensions()
                grid: dict[int, list[str]] = {}
                for number, row in enumerate(sheet.iter_rows(), start=1):
                    cells = [_cell(c) for c in row]
                    while cells and not cells[-1]:
                        cells.pop()
                    if any(cells):
                        grid[number] = cells
                if not grid:
                    continue
                fill_merged(grid, merged_ranges(sheet))
                rows = [grid[number] for number in sorted(grid)]
                start = first_data_row(rows)
                if start:
                    # A preamble row whose filled cells all say the same is a title merged over part of the
                    # table: written once, not once per column (ADR-0413).
                    for index in range(start):
                        filled = [c for c in rows[index][1:] if c]
                        if len(filled) >= 2 and len(set(filled)) == 1:
                            rows[index] = [rows[index][0], filled[0]]
                lines = [" | ".join(cells) for cells in rows]
                if start:
                    # The sheet's name and the table's preamble (titles and every header row) open each chunk.
                    preamble = [line for line in lines[:start] if line.strip(" |")]
                    # Each column's full label (ADR-0413), from the header rows: those of two cells or more.
                    heads = [r for r in rows[:start] if sum(1 for c in r[1:] if c) >= 2]
                    columns = [f"[columns] {' | '.join(column_labels(heads))}"] if heads else []
                    header, data = "\n".join([f"[{sheet.title}]", *preamble, *columns]), lines[start:]
                else:
                    at = header_index(rows)
                    header, data = f"[{sheet.title}]\n{lines[at]}", lines[:at] + lines[at + 1 :]
                segments.append(
                    Segment(text="\n".join(data) or header, section=sheet.title, repeat=header if data else None)
                )
        except DefusedXmlException as exc:  # a sheet is parsed as it is read
            raise ExtractionFailed("xml_unsafe", detail=type(exc).__name__) from exc
        except (KeyError, zipfile.BadZipFile) as exc:  # a sheet's XML missing from the archive
            raise ExtractionFailed("corrupt_file", detail=type(exc).__name__) from exc
        finally:
            workbook.close()
        if not segments:
            raise ExtractionFailed("empty_document")
        return ExtractedDocument(kind="xlsx", segments=segments)
