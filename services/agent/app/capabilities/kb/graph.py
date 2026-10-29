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



def _searches(request: CapabilityInput, retrieval: dict) -> tuple[list[tuple[str, int]], int]:
    """(query, k) of each search and the cap on the turn's passages. A text answer: the message at top_k.
    A report (ADR-0401, ADR-0403): the message at report_top_k, on an edit the report's title, then one
    search per planned chart or table at report_section_k."""
    if request.output != "report":
        return [(request.message, int(retrieval.get("top_k", 8)))], int(retrieval.get("top_k", 8))
    section = int(retrieval.get("report_section_k", 4))
    searches = [(request.message, int(retrieval.get("report_top_k", 12)))]
    if request.report_title:
        searches.append((request.report_title, section))
    searches += [(query, section) for query in request.report_queries]
    return searches, int(retrieval.get("report_max_passages", 40))


def _cited(request: CapabilityInput) -> list[Source]:
    """On an edit, the passages the current version cited that are this capability's kind (ADR-0402): the
    core fetches their text again from the backend under this turn's scope, or drops them."""
    cited: list[Source] = []
    for item in request.report_sources:
        if item.get("kind") != "kb":
            continue
        try:
            cited.append(Source.model_validate(item))
        except ValueError:
            continue
    return cited


async def _run(state: _State, runtime: Runtime[RuntimeContext]) -> dict:
    rt = runtime.context
    request = state["input"]
    # The number of passages comes from the capability's own configuration (C3).
    retrieval = rt.config.get("retrieval") or {}
    searches, budget = _searches(request, retrieval)
    hits: dict[str, dict] = {}
    failures = 0
    for query, size in searches:
        result = await rt.broker.execute("kb_search", {"query": query[:8000], "k": size})
        if result.status != "ok":
            failures += 1
            continue
        for hit in result.sources:
            hits.setdefault(hit["id"], hit)
    unavailable = failures == len(searches)
    found = [] if unavailable else [_source(hit) for hit in hits.values()]
    # An edit's cited passages first, then the searches' in order, up to the turn's cap (ADR-0402).
    cited = _cited(request)
    seen = {s.id for s in cited}
    sources = (cited + [s for s in found if s.id not in seen])[:budget]
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
