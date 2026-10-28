"""The kb capability's retrieval tool (phase 08, I4): one search across the turn's selected and
permitted knowledge bases through the document facade the broker hands it (ADR-0231), executed by
the broker, so its result passes POST_TOOL with sanitize_untrusted first (README §10.3). The tool
names no tenant, KB, collection or backend: the facade is bound to the scope the broker built from
the verified principal and the turn's selected KBs (S1), drops what a deleted KB held (I7), and
puts the latest version of a source first (D3). It returns references with each document's KB,
title, publisher, date, page and, for an older version, the version that supersedes it; no chunk
text: the answer step fetches it (S4)."""

from pydantic import BaseModel, ConfigDict, Field

from app.tools.spec import ToolContext, ToolFailure, ToolOutput, ToolSpec


class SearchArgs(BaseModel):
    # No tenant, user, KB or collection field: a call naming one fails validation (S1).
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=8000)
    k: int = Field(default=8, ge=1, le=20)


def _superseded(entry) -> str | None:
    title = getattr(entry, "superseded_by_title", None)
    if not title:
        return None
    date = getattr(entry, "superseded_by_date", None)
    return f"{title} ({date})" if date else title


async def search(args: SearchArgs, ctx: ToolContext) -> ToolOutput:
    if ctx.documents is None:
        raise ToolFailure("tool_unavailable")
    found = await ctx.documents.search(args.query, args.k)
    sources = []
    for chunk in found:
        hit, entry = chunk.hit, chunk.document
        if entry is None:  # not a catalogued KB document: never cited (I7)
            continue
        sources.append(
            {
                "id": hit.ref,
                "backend": chunk.backend,
                "collection": hit.collection,
                "attachment_id": hit.document_id,
                "title": entry.title,
                "page": hit.page,
                "locator": hit.section,
                "score": round(hit.score, 6),
                "kb": entry.kb,
                "kb_name": dict(entry.kb_name),
                "publisher": entry.publisher,
                "published_on": entry.published_on,
                "url": entry.source_url,
                "superseded_by": _superseded(entry),
            }
        )
    return ToolOutput(content="", sources=sources)


def tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="kb_search",
            description_ar="البحث في قواعد المعرفة المختارة",
            description_en="Search the selected knowledge bases",
            input_schema=SearchArgs,
            risk="read",
            required_permission="cap:kb",
            timeout_s=45,
            max_retries=0,
            handler=search,
        )
    ]
