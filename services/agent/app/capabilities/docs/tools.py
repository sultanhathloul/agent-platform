"""The docs capability's retrieval tool (ADR-0136): a search of the conversation's files through
the document facade the broker hands it (ADR-0231), executed by the broker, so its result passes
POST_TOOL with sanitize_untrusted first (README §10.3). The tool names no owner, collection or
backend: the facade is bound to the scope the broker built from the verified principal and the
conversation (S1), and searches each file on the backend that indexed it (ADR-0232). It returns
what it found as references with their file name, page and section, and no chunk text: the
answer step fetches the text when it assembles the answer (S4)."""

from pydantic import BaseModel, ConfigDict, Field

from app.tools.spec import ToolContext, ToolFailure, ToolOutput, ToolSpec


class SearchArgs(BaseModel):
    # No tenant, user or collection field: a call naming another owner fails validation (S1).
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=8000)
    k: int = Field(default=8, ge=1, le=20)


async def search(args: SearchArgs, ctx: ToolContext) -> ToolOutput:
    # The collections are the broker's: this conversation's files, never a knowledge base, since
    # the manifest declares documents.collections [session] (I1).
    if ctx.documents is None:
        raise ToolFailure("tool_unavailable")
    found = await ctx.documents.search(args.query, args.k)
    sources = [
        {
            "id": h.ref,
            "backend": chunk.backend,
            "collection": h.collection,
            "attachment_id": h.document_id,
            "title": h.file_name,
            "page": h.page,
            "locator": h.section,
            "score": round(h.score, 6),
        }
        for chunk in found
        for h in (chunk.hit,)
    ]
    return ToolOutput(content="", sources=sources)


def tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="docs_search",
            description_ar="البحث في الملفات المرفقة بالمحادثة",
            description_en="Search the files attached to the conversation",
            input_schema=SearchArgs,
            risk="read",
            required_permission="cap:docs",
            timeout_s=45,
            max_retries=0,
            handler=search,
        )
    ]
