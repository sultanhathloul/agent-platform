"""docs (UC3, README §9.3): answers from the files attached to the conversation.

One broker call to `docs_search` (a search of the conversation's indexed files through the
DocumentBackend, C6; the broker scopes it to the principal and the conversation, S1). The
capability returns what it found as source references (document, page, section, chunk
reference) and no text: the answer step fetches the chunks' text through the backend, checks
it at POST_TOOL and renders it inside untrusted delimiters (owner decision S4). Its findings
are notes only: that the passages may not answer the question, the files still pending or
failed, a search that failed, so the answer never presents general knowledge as the files'
content; the capability never waits for ingestion. No model call: the core answer node
answers on this capability's tier floor (ADR-0014)."""

from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from app.capabilities.contract import CapabilityInput, CapabilityOutput, RuntimeContext, Source
from app.models.prompts import PromptLibrary

PROMPTS = PromptLibrary.load(Path(__file__).resolve().parent / "prompts")


class _State(TypedDict, total=False):
    input: CapabilityInput
    output: CapabilityOutput


def _names(attachments: list[dict], status: str) -> list[str]:
    return [str(a.get("name") or "") for a in attachments if a.get("status") == status]


async def _run(state: _State, runtime: Runtime[RuntimeContext]) -> dict:
    rt = runtime.context
    request = state["input"]
    attachments = request.attachments
    indexed = [a for a in attachments if a.get("status") == "indexed"]
    sources: list[Source] = []
    unavailable = False
    if indexed:
        # The number of passages comes from the capability's own configuration (C3).
        retrieval = rt.config.get("retrieval") or {}
        # A report builds every figure from the passages, so it reads deeper (ADR-0401).
        k = int(retrieval.get("report_top_k", 20) if request.output == "report" else retrieval.get("top_k", 8))
        # An edit searches the report's subject at the report's depth, then the request ("add a pie chart by
        # sex") at a text answer's, so the figures the page already shows are fetched again (ADR-0401).
        searches = [(request.report_title, k), (request.message, int(retrieval.get("top_k", 8)))] if request.report_title else [(request.message, k)]
        hits: dict[str, dict] = {}
        failures = 0
        for query, size in searches:
            result = await rt.broker.execute("docs_search", {"query": query[:8000], "k": size})
            if result.status != "ok":
                failures += 1
                continue
            for hit in result.sources:
                hits.setdefault(hit["id"], hit)
        unavailable = failures == len(searches)
        if not unavailable:
            sources = [
                Source(
                    id=hit["id"],
                    ref=hit["id"],
                    title=hit.get("title") or "",
                    kind="doc",
                    locator=hit.get("locator"),
                    page=hit.get("page"),
                    document_id=hit["attachment_id"],
                    score=hit.get("score"),
                    backend=hit.get("backend"),
                    collection=hit.get("collection"),
                )
                for hit in hits.values()
            ]
    pending, failed = _names(attachments, "pending"), _names(attachments, "failed")
    findings = ""
    if sources or pending or failed or unavailable:
        findings = PROMPTS.render(
            "docs.evidence",
            request.locale,
            found=bool(sources),
            pending=pending,
            failed=failed,
            unavailable=unavailable,
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
