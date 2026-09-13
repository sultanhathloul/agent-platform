"""validate (README §5.2): deterministic checks on the draft answer. Citations
must be a subset of this turn's capability sources, and an Arabic turn must not
come back in another script. In phase 08 a failure feeds reflection and tier
escalation; until then it ends the turn."""

from langgraph.runtime import Runtime
from opentelemetry import trace

from app.errors import TurnError
from app.graph.deps import TurnContext
from app.graph.nodes import traced
from app.graph.state import AgentState
from app.text.script import contains_cjk

MAX_ANSWER_CHARS = 32_000


def validation_issues(
    answer: str | None, citations: list[dict], sources: list[dict], *, locale: str = "en"
) -> list[str]:
    issues = []
    text = (answer or "").strip()
    if not text:
        issues.append("empty_answer")
    else:
        if len(text) > MAX_ANSWER_CHARS:
            issues.append("answer_too_long")
        # Small models drift into Chinese on Arabic prompts (ADR-0024). The
        # answer is wrong, not merely badly phrased, so it never reaches the user.
        if locale == "ar" and contains_cjk(text):
            issues.append("cjk_in_arabic_answer")
    source_ids = {s["id"] for s in sources}
    if any(c.get("id") not in source_ids for c in citations):
        issues.append("citation_not_in_sources")
    return issues


@traced("validate")
async def validate(state: AgentState, runtime: Runtime[TurnContext]) -> dict:
    sources = [s for output in state.get("capability_outputs") or [] for s in output.get("sources", [])]
    issues = validation_issues(
        state.get("draft_answer"),
        state.get("citations") or [],
        sources,
        locale=state.get("locale") or "en",
    )
    span = trace.get_current_span()
    span.set_attribute("validate.passed", not issues)
    if issues:
        span.set_attribute("validate.issues", ",".join(issues))
        raise TurnError("validation_failed", detail=",".join(issues))
    return {}
