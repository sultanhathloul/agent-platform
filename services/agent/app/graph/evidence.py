"""Chunk text for the answer (owner decision S4, ADR-0159). Capabilities return source
references, never chunk text: what a checkpoint stores stays small and holds no document. The
answer step fetches each referenced chunk's text through the DocumentBackend right before it
assembles the answer, scoped to the verified principal and the conversation, and the text
passes POST_TOOL (sanitize_untrusted first, then the policies, per principal) on the way in,
under the scope of the capability that found it, as any tool output does (README §10.3).

The texts are kept on the turn context, never in state: page attribution after the answer
compares the answer with them (owner decision C1)."""

from typing import Any

from opentelemetry import trace

from app.errors import TurnError
from app.graph.deps import TurnContext
from app.graph.guarding import guard_context
from app.graph.state import AgentState
from app.guardrails.checkpoints import Checkpoint
from app.rag.backend import DocumentScope
from app.tools.spec import ToolOutput

# Opens each chunk in the text POST_TOOL checks at once; the text is split back on it.
# Angle brackets would be stripped as tags, so none are used.
_MARK = "⟦chunk {index}⟧"


def _split(content: str, count: int) -> list[str | None]:
    """The text of each chunk by position, None for one whose mark did not survive."""
    marks = [(content.find(_MARK.format(index=i)), i) for i in range(count)]
    found = sorted((pos, i) for pos, i in marks if pos >= 0)
    texts: list[str | None] = [None] * count
    for position, (pos, index) in enumerate(found):
        start = pos + len(_MARK.format(index=index))
        end = found[position + 1][0] if position + 1 < len(found) else len(content)
        texts[index] = content[start:end].strip()
    return texts


async def load_evidence(state: AgentState, ctx: TurnContext) -> list[dict[str, Any]]:
    """This turn's referenced chunks as Context Assembly's `retrieved` items, in each
    capability's order: {id, document_id, source, page, locator, score, text}."""
    span = trace.get_current_span()
    scope = DocumentScope(
        tenant_id=ctx.principal.tenant_id,
        user_id=ctx.principal.user_id,
        conversation_id=state["conversation_id"],
    )
    retrieved: list[dict[str, Any]] = []
    evidence: dict[str, str] = {}
    for output in state.get("capability_outputs") or []:
        sources = [s for s in output.get("sources") or [] if s.get("ref")]
        if not sources:
            continue
        fetched = await ctx.services.documents.fetch([s["ref"] for s in sources], scope)
        sources = [s for s in sources if s["ref"] in fetched]
        if not sources:
            continue
        content = "\n".join(f"{_MARK.format(index=i)}\n{fetched[s['ref']]}" for i, s in enumerate(sources))
        outcome = await ctx.services.guard.evaluate(
            Checkpoint.POST_TOOL,
            ToolOutput(content=content),
            principal=ctx.principal,
            context=guard_context(state, ctx, tools=(output["capability"],)),
        )
        ctx.report_guard(outcome.events)
        if outcome.unavailable:
            raise TurnError("guard_unavailable", retryable=True, detail=f"POST_TOOL: {outcome.unavailable}")
        if outcome.blocked:
            # A POST_TOOL policy withheld this capability's evidence: the answer gets none of it.
            span.add_event("evidence.withheld", {"capability": output["capability"]})
            continue
        for source, text in zip(sources, _split(outcome.payload.content, len(sources)), strict=True):
            if not text:
                continue
            evidence[source["ref"]] = text
            retrieved.append(
                {
                    "id": source["ref"],
                    "document_id": source.get("document_id"),
                    "source": source.get("title") or "",
                    "page": source.get("page"),
                    "locator": source.get("locator"),
                    "score": source.get("score"),
                    "text": text,
                }
            )
    ctx.evidence = evidence
    span.set_attribute("evidence.chunks", len(retrieved))
    return retrieved
