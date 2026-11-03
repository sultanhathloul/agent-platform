"""The recall capability's tools (report library PR, owner decisions 2, 3 and 5; ADR-0431 to ADR-0434),
executed by the broker, so each result passes POST_TOOL with sanitize_untrusted first (README §10.3): a
report's text and an earlier message are data the model wrote or the user sent.

No tool names a tenant, a user or a conversation: the broker hands each call the caller's own report library
and the turn's own conversation history, bound to the scope it built from the verified principal (S1). A
report id that is not the caller's is the same `report_not_found` as one that does not exist. Every output
is size-capped by config/library.yaml (rule 12d), and each call is bounded by its `timeout_s`."""

import asyncio
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.tools.spec import Label, ToolContext, ToolFailure, ToolOutput, ToolSpec

UUID = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"


class SearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Empty: the latest reports. No owner or conversation field: a call naming one fails validation (S1).
    query: str = Field(default="", max_length=2000)
    scope: Literal["conversation", "library"] = "library"
    k: int = Field(default=5, ge=1, le=5)


class ReadArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_id: str = Field(pattern=UUID)
    version: int | None = Field(default=None, ge=1, le=100000)
    locale: Literal["ar", "en"] = "ar"


class RecallArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)


async def report_search(args: SearchArgs, ctx: ToolContext) -> ToolOutput:
    if ctx.library is None:
        raise ToolFailure("tool_unavailable")
    hits = await ctx.library.search(args.query, this_conversation=args.scope == "conversation", limit=args.k)
    data = [
        {
            "report_id": h.report_id,
            "version": h.version,
            "latest_version": h.latest_version,
            "title": h.title,
            "subject": h.subject,
            "this_conversation": h.conversation_id == ctx.library.conversation_id,
            "updated_at": h.updated_at.date().isoformat(),
            "sources": h.sources[:6],
            "sections": [s.get("title") for s in h.sections if s.get("title")][:12],
            "matched": h.score > 0,
        }
        for h in hits
    ]
    return ToolOutput(content="", data=data, observed={"results": len(data)})


async def report_read(args: ReadArgs, ctx: ToolContext) -> ToolOutput:
    if ctx.library is None:
        raise ToolFailure("tool_unavailable")
    found = await ctx.library.read(args.report_id, args.version, args.locale)
    if found is None:
        raise ToolFailure("report_not_found")
    return ToolOutput(
        content=found.text,
        data={
            "report_id": found.report_id,
            "version": found.version,
            "latest_version": found.latest_version,
            "title": found.title,
            "this_conversation": found.this_conversation,
        },
        observed={"chars": len(found.text)},
    )


async def conversation_recall(args: RecallArgs, ctx: ToolContext) -> ToolOutput:
    if ctx.history is None:
        raise ToolFailure("tool_unavailable")
    # Linear and capped (config/library.yaml recall), in a worker thread so the event loop stays free.
    found = await asyncio.to_thread(ctx.history.search, args.query)
    data = [{"exchange": r.exchange, "role": r.role, "cut": r.cut, "text": r.text} for r in found]
    return ToolOutput(content="", data=data, observed={"results": len(data)})


def tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="report_search",
            description_ar="البحث في تقارير المستخدم",
            description_en="Search the user's reports",
            input_schema=SearchArgs,
            risk="read",
            required_permission="cap:recall",
            timeout_s=10,
            max_retries=0,
            handler=report_search,
            label=Label(ar="البحث في تقاريرك", en="Searching your reports"),
        ),
        ToolSpec(
            name="report_read",
            description_ar="قراءة تقرير من تقارير المستخدم",
            description_en="Read one of the user's reports",
            input_schema=ReadArgs,
            risk="read",
            required_permission="cap:recall",
            timeout_s=10,
            max_retries=0,
            handler=report_read,
            label=Label(ar="قراءة التقرير", en="Reading the report"),
        ),
        ToolSpec(
            name="conversation_recall",
            description_ar="البحث في رسائل هذه المحادثة السابقة",
            description_en="Search this conversation's earlier messages",
            input_schema=RecallArgs,
            risk="read",
            required_permission="cap:recall",
            timeout_s=10,
            max_retries=0,
            handler=conversation_recall,
            label=Label(ar="البحث في المحادثة", en="Searching the conversation"),
        ),
    ]
