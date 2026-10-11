"""The docs capability's retrieval tool (ADR-0136): hybrid search with rerank over the
conversation's session collection, executed by the broker, so its result passes
POST_TOOL with sanitize_untrusted first (README §10.3). The handler runs the shared
retrieval library (app/rag); the owner and the collection come from the capability's
own code (the principal and the attachments the core loaded), never from a model."""

from pydantic import BaseModel, Field

from app.rag import runtime
from app.rag.types import Owner
from app.tools.spec import ToolOutput, ToolSpec

UUID = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
# A line that opens each chunk in the tool's content; the capability splits on it after
# sanitize_untrusted ran. Angle brackets would be stripped as tags, so none are used.
CHUNK_MARK = "⟦chunk {id}⟧"


class SearchArgs(BaseModel):
    collection: str = Field(pattern=r"^sess_[0-9a-f-]{36}$")
    attachment_ids: list[str] = Field(min_length=1, max_length=10)
    query: str = Field(min_length=1, max_length=8000)
    tenant_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)


async def search(args: SearchArgs) -> ToolOutput:
    hits = await runtime.retriever().retrieve(
        Owner(tenant_id=args.tenant_id, user_id=args.user_id),
        args.collection,
        args.query,
        document_ids=args.attachment_ids,
    )
    content = "\n".join(f"{CHUNK_MARK.format(id=h.id)}\n{h.text}" for h in hits)
    sources = [
        {
            "id": h.id,
            "attachment_id": h.attachment_id,
            "page": h.page,
            "section": h.section,
            "ord": h.ord,
            "score": round(h.score, 6),
        }
        for h in hits
    ]
    return ToolOutput(content=content, sources=sources)


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
