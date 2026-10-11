"""validate (README §5.2): deterministic checks on the draft answer. Citations are
attributed here, after the answer is complete: a source's page is cited when its text
shares enough word n-grams with the answer (ADR-0140, config/docs.yaml `citations`).
Citations must be a subset of this turn's capability sources, and an answer must keep to
the writing scripts its locale allows (config/validation.yaml, ADR-0060). The
answer node has already retried a script failure once (ADR-0061); a failure
here ends the turn, retracting any text already streamed (ADR-0039). In phase 08
a failure also feeds reflection and tier escalation."""

from langgraph.runtime import Runtime
from opentelemetry import trace

from app.errors import TurnError
from app.graph.deps import TurnContext
from app.graph.nodes import emit, retract_streamed, traced
from app.graph.state import AgentState
from app.rag.citations import attribute
from app.text.script import ScriptPolicy, script_issues

MAX_ANSWER_CHARS = 32_000


def validation_issues(
    answer: str | None, citations: list[dict], sources: list[dict], *, scripts: ScriptPolicy | None = None
) -> list[str]:
    issues = []
    text = (answer or "").strip()
    if not text:
        issues.append("empty_answer")
    else:
        if len(text) > MAX_ANSWER_CHARS:
            issues.append("answer_too_long")
        if scripts is not None:
            issues.extend(script_issues(text, scripts))
    source_ids = {s["id"] for s in sources} | {s["document_id"] for s in sources if s.get("document_id")}
    if any(c.get("id") not in source_ids for c in citations):
        issues.append("citation_not_in_sources")
    return issues


@traced("validate")
async def validate(state: AgentState, runtime: Runtime[TurnContext]) -> dict:
    sources = [s for output in state.get("capability_outputs") or [] for s in output.get("sources", [])]
    rule = runtime.context.services.docs_config.citations
    citations = attribute(
        state.get("draft_answer") or "", sources, ngram=rule.ngram, min_shared=rule.min_shared_ngrams
    )
    issues = validation_issues(
        state.get("draft_answer"),
        citations,
        sources,
        scripts=runtime.context.services.validation.scripts_for(state.get("locale") or "en"),
    )
    span = trace.get_current_span()
    span.set_attribute("validate.passed", not issues)
    if issues:
        span.set_attribute("validate.issues", ",".join(issues))
        retract_streamed(state, runtime, "validation_failed")
        raise TurnError("validation_failed", detail=",".join(issues))
    span.set_attribute("validate.citations", len(citations))
    span.set_attribute("validate.cited_pages", sum(len(c["pages"]) for c in citations))
    for citation in citations:
        emit(runtime, "citation", citation)
    return {"citations": citations}
