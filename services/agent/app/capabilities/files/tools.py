"""The files capability's tools (my-files PR, owner decision 2; ADR-0443), executed by the broker, so each
result
passes POST_TOOL with sanitize_untrusted first (README §10.3): a file name and a conversation's title are text
the user wrote, data like any tool output.

No tool names a tenant, a user or a conversation: the broker hands each call the caller's own file library,
bound to the scope it built from the verified principal (S1). A file id that is not the caller's is the same
`file_not_found` as one that does not exist. Both tools return references, never a file's text: the sources
carry chunk ids, and the answer step fetches their text through the document backend under the turn's scope
and
checks it at POST_TOOL under the capability's guard scopes (S4, ADR-0159, ADR-0445), so the phase 04 citations
attribute the answer's pages as for an attached file. Every output is size-capped by config/file_library.yaml
(rule 12d), and each call is bounded by its `timeout_s`."""

import re

from pydantic import BaseModel, ConfigDict, Field

from app.tools.spec import Label, ToolContext, ToolFailure, ToolOutput, ToolSpec

_EXTENSION = re.compile(r"\.(pdf|docx|xlsx|txt|md)$", re.IGNORECASE)


def title_of(name: str) -> str:
    return _EXTENSION.sub("", name).strip() or name


UUID = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"


class SearchArgs(BaseModel):
    # No owner, conversation or collection field: a call naming one fails validation (S1).
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=8000)
    k: int = Field(default=8, ge=1, le=10)


class ReadArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_id: str = Field(pattern=UUID)
    pages: list[int] | None = Field(default=None, max_length=10)


def _file(entry, *, this_conversation: str) -> dict:
    record = entry.record
    started = entry.conversation_created_at or record.created_at
    return {
        "file_id": record.id,
        "name": record.name,
        # What the answer cites: the name without its extension, so an Arabic name stays one script
        # («تحليل-الدم», not «تحليل-الدم.pdf», which the writing-script check refuses, ADR-0060).
        "title": title_of(record.name),
        "kind": record.kind,
        "conversation_id": record.conversation_id,
        "conversation_title": entry.conversation_title or "",
        "conversation_date": started.date().isoformat(),
        "uploaded_on": record.created_at.date().isoformat(),
        "this_conversation": record.conversation_id == this_conversation,
    }


def _source(entry, chunk) -> dict:
    record = entry.record
    return {
        "id": chunk.ref,
        "backend": record.backend,
        "collection": chunk.collection,
        "attachment_id": record.id,
        "title": record.name,
        "page": chunk.page,
        "locator": chunk.section,
        "score": round(chunk.score, 6),
    }


async def file_search(args: SearchArgs, ctx: ToolContext) -> ToolOutput:
    if ctx.files is None:
        raise ToolFailure("tool_unavailable")
    hits = await ctx.files.search(args.query, args.k)
    files: dict[str, dict] = {}
    for hit in hits:
        files.setdefault(hit.entry.record.id, _file(hit.entry, this_conversation=ctx.files.conversation_id))
    return ToolOutput(
        content="",
        sources=[_source(h.entry, h.chunk) for h in hits],
        data=list(files.values()),
        observed={"results": len(hits), "files": len(files)},
    )


async def file_read(args: ReadArgs, ctx: ToolContext) -> ToolOutput:
    if ctx.files is None:
        raise ToolFailure("tool_unavailable")
    found = await ctx.files.read(args.file_id, args.pages)
    if found is None:
        raise ToolFailure("file_not_found")
    entry, chunks = found
    return ToolOutput(
        content="",
        sources=[_source(entry, c) for c in chunks],
        data=_file(entry, this_conversation=ctx.files.conversation_id),
        observed={"chunks": len(chunks)},
    )


def tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="file_search",
            description_ar="البحث في ملفات المستخدم في كل محادثاته",
            description_en="Search the user's files of every conversation",
            input_schema=SearchArgs,
            risk="read",
            required_permission="cap:files",
            # One query embedding and, when switched on, one rerank (the facade-search-in-tool rule).
            timeout_s=40,
            max_retries=0,
            handler=file_search,
            label=Label(ar="البحث في ملفاتك", en="Searching your files"),
        ),
        ToolSpec(
            name="file_read",
            description_ar="قراءة ملف من ملفات المستخدم",
            description_en="Read one of the user's files",
            input_schema=ReadArgs,
            risk="read",
            required_permission="cap:files",
            # Bounded like file_search, under the same facade rule (it reads the index, no embedding).
            timeout_s=40,
            max_retries=0,
            handler=file_read,
            label=Label(ar="قراءة الملف", en="Reading the file"),
        ),
    ]
