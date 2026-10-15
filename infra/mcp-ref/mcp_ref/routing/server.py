"""mcp-routing-ref (I10, ADR-0211): the routing fixture over MCP Streamable HTTP, official SDK.

Tools: `geocode {query}` → `{query, best, matches[≤3]}` (each match `{id, name, name_en, point,
score, description}`; `best` only when one match is strong and clearly ahead), and `route {from,
to, profile: car|foot}` → `{from, to, profile, distance_km, duration_min, steps[]}`. Deterministic.
It applies no policy (ADR-0158): what it returns is data the platform checks.

A test-only switch, off in compose: MCP_REF_TEST_DELAY_S adds a delay to every call, and
MCP_REF_TEST_OVERSIZE_BYTES pads every result past the platform's cap (resilience tests, I10)."""

import asyncio
import os
from typing import Annotated, Literal

import uvicorn
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp_ref.routing.graph import Fixture


class Match(BaseModel):
    id: str
    name: str
    name_en: str
    point: list[float]
    score: float
    description: str


class Geocoded(BaseModel):
    query: str
    best: Match | None
    matches: list[Match]
    padding: str | None = None


class Step(BaseModel):
    from_: str = Field(alias="from")
    to: str
    distance_km: float


class Routed(BaseModel):
    found: bool
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    profile: str | None = None
    distance_km: float | None = None
    duration_min: int | None = None
    steps: list[Step] = []
    padding: str | None = None


def build(
    *, delay_s: float | None = None, oversize: int | None = None, host: str = "0.0.0.0", port: int = 8000
):
    delay = float(os.environ.get("MCP_REF_TEST_DELAY_S", "0") if delay_s is None else delay_s)
    pad = int(os.environ.get("MCP_REF_TEST_OVERSIZE_BYTES", "0") if oversize is None else oversize)
    fixture = Fixture()
    mcp = FastMCP("mcp-routing-ref", host=host, port=port, log_level="WARNING")

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @mcp.tool()
    async def geocode(query: Annotated[str, Field(min_length=1, max_length=200)]) -> Geocoded:
        """Finds up to three places of the fixture by name (Arabic, Gulf or English)."""
        if delay:
            await asyncio.sleep(delay)
        return Geocoded(**fixture.geocode(query), padding="x" * pad if pad else None)

    @mcp.tool()
    async def route(
        from_: Annotated[str, Field(validation_alias="from", min_length=1, max_length=200)],
        to: Annotated[str, Field(min_length=1, max_length=200)],
        profile: Literal["car", "foot"] = "car",
    ) -> Routed:
        """The shortest route between two places of the fixture by id or exact name."""
        if delay:
            await asyncio.sleep(delay)
        found = fixture.route(from_, to, profile)
        padding = "x" * pad if pad else None
        if found is None:
            return Routed(found=False, padding=padding)
        return Routed.model_validate({"found": True, **found, "padding": padding})

    return mcp


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    app = build(port=port).streamable_http_app()
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
