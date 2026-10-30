"""The report spec (owner decision A on PR #17, ADR-0406): the report writer answers with JSON, never HTML.

A spec is a title, an optional subtitle and period, its language and ordered sections: `kpi_row`, `chart`,
`table`, `text` (Markdown) and `callout`. Every kpi_row, chart and table names in `sources` the passages it
used, by the ids the context gave them ("P1", "P2"…), or "general". These models are the schema:
config/artifacts/report-spec.schema.json is generated from them (`python -m app.artifacts.spec`), and a test
holds the two equal. The platform renders a spec to HTML itself (app/artifacts/render.py).

`SectionReader` reads the model's JSON as it streams: the spec's head once the `sections` array opens, then
each section as soon as its object closes, so a section is checked and shown while the next is written.
Linear in the text, with the model's output capped before it is read (I4)."""

import json
import re
import sys
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

SCHEMA_ID = "report-spec"
SCHEMA_VERSION = "1.0.0"
SourceId = Annotated[str, Field(pattern=r"^(P[1-9][0-9]{0,2}|general)$")]
Number = float | int
Short = Annotated[str, Field(max_length=200)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Kpi(_Strict):
    label: Short
    value: Number | None
    unit: Annotated[str, Field(max_length=16)] | None = None
    delta: Number | None = None


class KpiRow(_Strict):
    kind: Literal["kpi_row"]
    title: Short | None = None
    items: Annotated[list[Kpi], Field(min_length=1, max_length=8)]
    sources: Annotated[list[SourceId], Field(min_length=1, max_length=12)]


class Series(_Strict):
    name: Short
    data: Annotated[list[Number | None], Field(max_length=200)]


class Chart(_Strict):
    kind: Literal["chart"]
    type: Literal["bar", "line", "area", "pie", "doughnut", "stacked_bar"]
    title: Short
    labels: Annotated[list[Short], Field(max_length=200)]
    series: Annotated[list[Series], Field(min_length=1, max_length=8)]
    unit: Annotated[str, Field(max_length=16)] | None = None
    sources: Annotated[list[SourceId], Field(min_length=1, max_length=12)]


Cell = Annotated[str, Field(max_length=300)] | Number | None


class Table(_Strict):
    kind: Literal["table"]
    title: Short
    columns: Annotated[list[Short], Field(min_length=1, max_length=12)]
    rows: Annotated[list[Annotated[list[Cell], Field(max_length=12)]], Field(max_length=100)]
    sources: Annotated[list[SourceId], Field(min_length=1, max_length=12)]


class Text(_Strict):
    kind: Literal["text"]
    title: Short | None = None
    markdown: Annotated[str, Field(max_length=4000)]
    # The passages its figures come from (ADR-0425): on a sourced turn each sentence's figures are checked
    # against these only; none, and a sentence stating a figure leaves the page.
    sources: Annotated[list[SourceId], Field(max_length=12)] = []


class Callout(_Strict):
    kind: Literal["callout"]
    tone: Literal["info", "success", "warning"] = "info"
    title: Short | None = None
    markdown: Annotated[str, Field(max_length=1000)]
    sources: Annotated[list[SourceId], Field(max_length=12)] = []


Section = Annotated[KpiRow | Chart | Table | Text | Callout, Field(discriminator="kind")]
SECTION = TypeAdapter(Section)


class ReportSpec(_Strict):
    title: Short
    subtitle: Short | None = None
    period: Annotated[str, Field(max_length=80)] | None = None
    language: Literal["ar", "en"]
    sections: Annotated[list[Section], Field(min_length=1, max_length=30)]


class Head(_Strict):
    """What precedes the sections, for the page's header before the first section arrives."""

    title: Short
    subtitle: Short | None = None
    period: Annotated[str, Field(max_length=80)] | None = None
    language: Literal["ar", "en"]


def schema() -> dict:
    out = ReportSpec.model_json_schema()
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{SCHEMA_ID}/{SCHEMA_VERSION}",
        **out,
    }


class SpecError(ValueError):
    """The model's answer is not a report spec (the turn retries once, then fails validation_failed)."""


_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


def parse(text: str) -> ReportSpec:
    """The whole answer as a spec: one JSON object, a Markdown fence around it tolerated."""
    body = _FENCE.sub("", text or "").strip()
    start = body.find("{")
    if start < 0:
        raise SpecError("no JSON object")
    try:
        raw = json.JSONDecoder().raw_decode(body[start:])[0]
        return ReportSpec.model_validate(raw)
    except (ValueError, ValidationError) as exc:
        raise SpecError(str(exc)[:500]) from None


class SectionReader:
    """Feeds on the streamed answer; `take()` returns the head (once) and each section completed since."""

    def __init__(self) -> None:
        self._text: list[str] = []
        self._buf = ""
        self._scan = 0  # where the scan for the sections array or the next section goes on
        self._array = -1  # index just after the sections array's "["
        self._depth = 0
        self._in_string = False
        self._escape = False
        self._start = -1  # the open section object's "{"
        self.head: Head | None = None
        self.head_error = False

    def feed(self, delta: str) -> None:
        self._buf += delta

    @property
    def text(self) -> str:
        return self._buf

    def take(self) -> list[Head | dict]:
        out: list[Head | dict] = []
        if self._array < 0:
            at = re.search(r'"sections"\s*:\s*\[', self._buf)
            if not at:
                return out
            self._array = self._scan = at.end()
            head = self._buf[: at.start()].rstrip().rstrip(",")
            start = head.find("{")
            try:
                self.head = Head.model_validate(json.loads(head[start:] + "}")) if start >= 0 else None
            except (ValueError, ValidationError):
                self.head = None
                self.head_error = True
            if self.head is not None:
                out.append(self.head)
        buf, i = self._buf, self._scan
        while i < len(buf):
            ch = buf[i]
            if self._in_string:
                if self._escape:
                    self._escape = False
                elif ch == "\\":
                    self._escape = True
                elif ch == '"':
                    self._in_string = False
            elif ch == '"':
                self._in_string = True
            elif ch == "{":
                if self._depth == 0:
                    self._start = i
                self._depth += 1
            elif ch == "}":
                self._depth -= 1
                if self._depth == 0 and self._start >= 0:
                    try:
                        raw = json.loads(buf[self._start : i + 1])
                    except ValueError:
                        raw = None
                    if isinstance(raw, dict):
                        out.append(raw)
                    self._start = -1
            i += 1
        self._scan = i
        return out


def section(raw: dict) -> KpiRow | Chart | Table | Text | Callout | None:
    """A streamed section validated against the schema, or None (it is left out; the final parse decides)."""
    try:
        return SECTION.validate_python(raw)
    except ValidationError:
        return None


if __name__ == "__main__":  # writes config/artifacts/report-spec.schema.json
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("../../config/artifacts/report-spec.schema.json")
    target.write_text(json.dumps(schema(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
