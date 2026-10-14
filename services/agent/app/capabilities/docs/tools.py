"""The docs capability's retrieval tool (ADR-0136): a search of the conversation's files through
the DocumentBackend (C6), executed by the broker, so its result passes POST_TOOL with
sanitize_untrusted first (README §10.3). The tool names no owner and no collection: the broker
injects the scope from the verified principal and the conversation (S1), and the backend
enforces it. It returns what it found as references with their file name, page and section,
and no chunk text: the answer step fetches the text when it assembles the answer (S4)."""

from pydantic import BaseModel, ConfigDict, Field

from app.rag.backend import DocumentScope
from app.tools.spec import ToolContext, ToolOutput, ToolSpec


class SearchArgs(BaseModel):
    # No tenant, user or collection field: a call naming another owner fails validation (S1).
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=8000)
    k: int = Field(default=8, ge=1, le=20)


async def search(args: SearchArgs, ctx: ToolContext) -> ToolOutput:
    scope = DocumentScope(
        tenant_id=ctx.scope.tenant_id, user_id=ctx.scope.user_id, conversation_id=ctx.scope.conversation_id
    )
    hits = await ctx.documents.search(args.query, scope, args.k)
    sources = [
        {
            "id": h.ref,
            "attachment_id": h.document_id,
            "title": h.file_name,
            "page": h.page,
            "locator": h.section,
            "score": round(h.score, 6),
        }
        for h in hits
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
