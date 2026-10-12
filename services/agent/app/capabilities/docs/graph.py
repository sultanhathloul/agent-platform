"""docs (UC3, README §9.3): answers from the files attached to the conversation.

One broker call to `docs_search` (a search of the conversation's indexed files through the
DocumentBackend, C6; the broker scopes it to the principal and the conversation, S1).
The chunks come back through POST_TOOL, sanitized (README §10.3); they are compressed
per chunk when over the evidence budget and rendered for the answer inside untrusted
delimiters (`docs.evidence`). Each chunk is a Source with its document, page and text, so
the core can attribute pages after the answer (ADR-0140). Files still pending or failed
are named in the evidence, and so is a search that failed, so the answer never presents
general knowledge as the files' content; the capability never waits for ingestion. No model call: the
core answer node answers on this capability's tier floor (ADR-0014)."""

from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from app.capabilities.contract import CapabilityInput, CapabilityOutput, RuntimeContext, Source
from app.capabilities.docs.tools import CHUNK_MARK
from app.context.tokens import default_counter
from app.models.prompts import PromptLibrary
from app.rag.config import load_docs_config
from app.rag.evidence import compress_texts
from app.settings import get_settings

PROMPTS = PromptLibrary.load(Path(__file__).resolve().parent / "prompts")
COUNTER = default_counter()


class _State(TypedDict, total=False):
    input: CapabilityInput
    output: CapabilityOutput


def split_content(content: str, ids: list[str]) -> dict[str, str]:
    """The sanitized text of each chunk, by id, from the tool's content."""
    texts: dict[str, str] = {}
    marks = [(content.find(CHUNK_MARK.format(id=i)), i) for i in ids]
    marks = sorted((pos, i) for pos, i in marks if pos >= 0)
    for index, (pos, chunk_id) in enumerate(marks):
        start = pos + len(CHUNK_MARK.format(id=chunk_id))
        end = marks[index + 1][0] if index + 1 < len(marks) else len(content)
        texts[chunk_id] = content[start:end].strip()
    return texts


def _name(attachment: dict) -> str:
    return str(attachment.get("name") or "").replace('"', "'")


async def _run(state: _State, runtime: Runtime[RuntimeContext]) -> dict:
    rt = runtime.context
    request = state["input"]
    locale = request.locale
    attachments = request.attachments
    indexed = [a for a in attachments if a.get("status") == "indexed"]
    pending = [_name(a) for a in attachments if a.get("status") == "pending"]
    failed = [_name(a) for a in attachments if a.get("status") == "failed"]
    names = {a["id"]: _name(a) for a in attachments}
    chunks: list[dict] = []
    sources: list[Source] = []
    unavailable = False
    if indexed:
        result = await rt.broker.execute("docs_search", {"query": request.message})
        unavailable = result.status != "ok"
        if not unavailable:
            texts = split_content(result.content, [s["id"] for s in result.sources])
            found = [s for s in result.sources if texts.get(s["id"])]
            budget = load_docs_config(get_settings().config_dir).retrieval.evidence_max_tokens
            compressed = compress_texts([texts[s["id"]] for s in found], request.message, budget, COUNTER)
            for source, text in zip(found, compressed, strict=True):
                name = names.get(source["attachment_id"], "")
                chunks.append(
                    {"name": name, "page": source.get("page"), "section": source.get("section"), "text": text}
                )
                sources.append(
                    Source(
                        id=source["id"],
                        title=name,
                        kind="doc",
                        locator=source.get("section"),
                        page=source.get("page"),
                        document_id=source["attachment_id"],
                        excerpt=texts[source["id"]],
                    )
                )
    findings = ""
    if chunks or pending or failed or unavailable:
        findings = PROMPTS.render(
            "docs.evidence", locale, chunks=chunks, pending=pending, failed=failed, unavailable=unavailable
        ).text
    runs = int(rt.cap_state.get("searches", 0)) + (1 if indexed else 0)
    return {"output": CapabilityOutput(findings=findings, sources=sources, cap_state={"searches": runs})}


def build() -> StateGraph:
    # Returns the uncompiled StateGraph; the core compiles it (ADR-0053).
    graph = StateGraph(_State, context_schema=RuntimeContext)
    graph.add_node("run", _run)
    graph.add_edge(START, "run")
    graph.add_edge("run", END)
    return graph
