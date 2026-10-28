"""attribute_citations (owner decision C1): the citations of the answer, before validate. A
capability may supply citations of its own; page attribution adds, capability-agnostically,
one per document whose retrieved chunks share enough word n-grams with the answer (ADR-0140,
config/citations.yaml), comparing against the chunk texts the answer step fetched (kept on
the turn context, never in state). validate then checks every citation against this turn's
sources: attribution never bypasses it."""

from langgraph.runtime import Runtime
from opentelemetry import trace

from app.graph.deps import TurnContext
from app.graph.nodes import traced
from app.graph.state import AgentState
from app.rag.citations import attribute


def _supplied(citation: dict) -> dict:
    """A capability's Citation in the shape of the `citation` event."""
    return {
        "id": citation["id"],
        "title": citation["title"],
        "source": citation.get("kind") or "doc",
        "url": citation.get("url"),
        "kb": citation.get("kb"),
        "locator": citation.get("locator"),
        "document_id": citation.get("document_id"),
        "pages": sorted(citation.get("pages") or []),
        **{key: citation[key] for key in ("kb_name", "publisher", "published_on") if citation.get(key) is not None},
    }


@traced("attribute_citations")
async def attribute_citations(state: AgentState, runtime: Runtime[TurnContext]) -> dict:
    ctx = runtime.context
    outputs = state.get("capability_outputs") or []
    sources = [s for output in outputs for s in output.get("sources", [])]
    citations = [_supplied(c) for output in outputs for c in output.get("citations", [])]
    attributed = attribute(
        state.get("draft_answer") or "",
        sources,
        ctx.evidence,
        ctx.services.citations,
        question=state.get("user_message") or "",
    )
    span = trace.get_current_span()
    span.set_attribute("citations.supplied", len(citations))
    span.set_attribute("citations.attributed", len(attributed))
    known = {c["id"] for c in citations}
    return {"citations": citations + [c for c in attributed if c["id"] not in known]}
