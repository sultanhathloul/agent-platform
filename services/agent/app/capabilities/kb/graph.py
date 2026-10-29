"""kb (UC4, README §9.4; phase 08): answers from the knowledge bases the user toggled.

One broker call to `kb_search` across the turn's selected and permitted KBs (I4). The capability
returns what it found as source references, each with its KB, title, publisher, date and page
(D3), and no text: the answer step fetches the chunks' text through the backend and renders it
inside untrusted delimiters that carry that metadata (S4). Its findings are notes: how to use the
sources (state a figure's source and date; when two versions disagree, use the latest and say so
if useful), that the passages may not answer the question, or that the search failed, so the
answer never presents general knowledge as a KB's content. No model call: the core answer node
answers on this capability's tier floor (ADR-0014)."""

from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from app.capabilities.contract import CapabilityInput, CapabilityOutput, Localized, RuntimeContext, Source
from app.models.prompts import PromptLibrary

PROMPTS = PromptLibrary.load(Path(__file__).resolve().parent / "prompts")


class _State(TypedDict, total=False):
    input: CapabilityInput
    output: CapabilityOutput


def _source(hit: dict) -> Source:
    name = hit.get("kb_name") or {}
    return Source(
        id=hit["id"],
        ref=hit["id"],
        title=hit.get("title") or "",
        kind="kb",
        kb=hit.get("kb"),
        url=hit.get("url"),
        locator=hit.get("locator"),
        page=hit.get("page"),
        document_id=hit["attachment_id"],
        score=hit.get("score"),
        backend=hit.get("backend"),
        collection=hit.get("collection"),
        kb_name=Localized(ar=name["ar"], en=name["en"]) if name.get("ar") and name.get("en") else None,
        publisher=hit.get("publisher"),
        published_on=hit.get("published_on"),
        superseded_by=hit.get("superseded_by"),
    )


async def _run(state: _State, runtime: Runtime[RuntimeContext]) -> dict:
    rt = runtime.context
    request = state["input"]
    # The number of passages comes from the capability's own configuration (C3).
    retrieval = rt.config.get("retrieval") or {}
    # A report builds every figure from the passages, so it reads deeper (ADR-0401).
    k = int(retrieval.get("report_top_k", 20) if request.output == "report" else retrieval.get("top_k", 8))
    # An edit searches the report's subject with the request ("add a pie chart" alone finds nothing).
    query = f"{request.report_title}\n{request.message}" if request.report_title else request.message
    result = await rt.broker.execute("kb_search", {"query": query[:8000], "k": k})
    unavailable = result.status != "ok"
    sources = [] if unavailable else [_source(hit) for hit in result.sources]
    findings = PROMPTS.render(
        "kb.evidence",
        request.locale,
        found=bool(sources),
        versions=any(s.superseded_by for s in sources),
        unavailable=unavailable,
    ).text
    runs = int(rt.cap_state.get("searches", 0)) + 1
    return {"output": CapabilityOutput(findings=findings, sources=sources, cap_state={"searches": runs})}


def build() -> StateGraph:
    # Returns the uncompiled StateGraph; the core compiles it (ADR-0053).
    graph = StateGraph(_State, context_schema=RuntimeContext)
    graph.add_node("run", _run)
    graph.add_edge(START, "run")
    graph.add_edge("run", END)
    return graph
