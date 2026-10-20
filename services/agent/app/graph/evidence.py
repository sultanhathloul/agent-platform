"""Chunk text for the answer (owner decision S4, ADR-0159). Capabilities return source
references, never chunk text: what a checkpoint stores stays small and holds no document. The
answer step fetches each referenced chunk's text right before it assembles the answer, through
the backend and from the collection that found it (I1, ADR-0202), scoped to the verified
principal; a collection outside what that capability may read this turn (the scope the broker
gives its tools) is never fetched, whatever a source names. The text passes POST_TOOL
(sanitize_untrusted first, then the policies, per principal) on the way in, under the scope of
the capability that found it, as any tool output does (README §10.3). A backend that reaches a
server over MCP does so through `broker.transport`; those calls join the turn's tool audit under
the capability that found the sources (ADR-0230).

The texts are kept on the turn context, never in state: page attribution after the answer
compares the answer with them (owner decision C1)."""

from collections import defaultdict
from typing import Any

from opentelemetry import trace

from app.errors import TurnError
from app.graph.deps import TurnContext
from app.graph.guarding import guard_context
from app.graph.state import AgentState
from app.guardrails.checkpoints import Checkpoint
from app.rag.backend import DocumentScope
from app.tools.broker import auditing
from app.tools.spec import ToolFailure, ToolOutput

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


async def _fetch(
    state: AgentState, ctx: TurnContext, capability: str, sources: list[dict[str, Any]]
) -> dict[tuple[str, str], str]:
    """The text of each source by (backend, ref), fetched from the backend and collection that
    found it. A source naming an unknown backend, or a collection this capability may not read
    this turn, gets nothing."""
    services = ctx.services
    span = trace.get_current_span()
    allowed = services.broker.scope_for(
        capability,
        ctx.principal,
        conversation_id=state["conversation_id"],
        selected_kbs=tuple(state.get("selected_kbs") or ()),
    ).collections
    groups: dict[tuple[str, str | None], list[str]] = defaultdict(list)
    for source in sources:
        backend = source.get("backend") or services.backend_name(capability) or ""
        groups[(backend, source.get("collection"))].append(source["ref"])
    texts: dict[tuple[str, str], str] = {}
    for (name, collection), refs in groups.items():
        backend = services.backends.get(name)
        if backend is None and name == getattr(services.documents, "name", None):
            backend = services.documents
        if backend is None or (collection is not None and collection not in allowed):
            span.add_event("evidence.refused", {"capability": capability, "backend": name})
            continue
        scope = DocumentScope(
            tenant_id=ctx.principal.tenant_id,
            user_id=ctx.principal.user_id,
            collections=(collection,) if collection is not None else allowed,
        )
        try:
            with auditing(capability, ctx.tool_audit.append):
                fetched = await backend.fetch(refs, scope)
        except ToolFailure as failure:
            # A server that cannot answer leaves these sources without text: the answer has less
            # evidence, and the failure is in the audit entry, by its code.
            span.add_event(
                "evidence.unavailable", {"capability": capability, "backend": name, "code": failure.code}
            )
            continue
        texts |= {(name, ref): text for ref, text in fetched.items() if ref in refs}
    return texts


def _key(source: dict[str, Any], ctx: TurnContext, capability: str) -> tuple[str, str]:
    return (source.get("backend") or ctx.services.backend_name(capability) or "", source["ref"])


async def load_evidence(state: AgentState, ctx: TurnContext) -> list[dict[str, Any]]:
    """This turn's referenced chunks as Context Assembly's `retrieved` items, in each
    capability's order: {id, document_id, source, page, locator, score, text}."""
    span = trace.get_current_span()
    retrieved: list[dict[str, Any]] = []
    evidence: dict[str, str] = {}
    for output in state.get("capability_outputs") or []:
        sources = [s for s in output.get("sources") or [] if s.get("ref")]
        if not sources:
            continue
        capability = output["capability"]
        fetched = await _fetch(state, ctx, capability, sources)
        sources = [s for s in sources if _key(s, ctx, capability) in fetched]
        if not sources:
            continue
        content = "\n".join(
            f"{_MARK.format(index=i)}\n{fetched[_key(s, ctx, capability)]}" for i, s in enumerate(sources)
        )
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
