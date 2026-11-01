"""A report's section plan (owner decision 2 on PR #17, ADR-0403): before its capabilities search, a report
turn over knowledge bases or files asks the `extractor` role (fast tier) for the sections, charts and
tables the page will need, each with a search query, so retrieval runs one search per planned chart or
table beside the general one. On an edit the plan reads the current version's title and headings, so the
charts the page keeps and the one the request adds are planned together.

The call goes through Context Assembly and LiteLLM like every model call; the message and the headings
reach the model as delimited data in the user role (`report.plan_request`). Its answer is JSON; anything
unusable, and a model that cannot answer, leave the plan empty: the general search alone runs."""

import json
import logging
import re

from opentelemetry import trace

from app.context.package import AssemblyRequest
from app.errors import TurnError
from app.graph.calls import call_model, prepare_call
from app.graph.deps import TurnContext
from app.graph.state import AgentState

log = logging.getLogger(__name__)

NODE = "report_plan"
ROLE = "extractor"
MAX_SECTIONS = 8
MAX_QUERY_CHARS = 300
_JSON = re.compile(r"\{.*\}", re.S)
_HEADING = re.compile(r"<(h[1-3]|title)[^>]*>(.*?)</\1>", re.S | re.I)
_CHART_TITLE = re.compile(r'"title"\s*:\s*"([^"]{1,200})"')
_TAG = re.compile(r"<[^>]+>")


def headings(page: str | None, limit: int = 24) -> list[str]:
    """The current version's title, headings and chart titles: what an edit's plan keeps."""
    if not page:
        return []
    found = [_TAG.sub("", m.group(2)).strip() for m in _HEADING.finditer(page)]
    found += [m.group(1).strip() for m in _CHART_TITLE.finditer(page)]
    return list(dict.fromkeys(h for h in found if h))[:limit]


def parse_plan(text: str) -> list[str]:
    """The model's `{"sections": [{"title", "query"}]}` as up to MAX_SECTIONS distinct queries."""
    match = _JSON.search(text or "")
    if not match:
        return []
    try:
        raw = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    sections = raw.get("sections") if isinstance(raw, dict) else None
    queries: list[str] = []
    for item in sections if isinstance(sections, list) else []:
        query = item.get("query") if isinstance(item, dict) else None
        if isinstance(query, str) and query.strip():
            query = query.strip()[:MAX_QUERY_CHARS]
            if query not in queries:
                queries.append(query)
    return queries[:MAX_SECTIONS]


async def plan_sections(state: AgentState, ctx: TurnContext, current_page: str | None) -> list[str]:
    span = trace.get_current_span()
    node = ctx.services.assembler.node(NODE)
    request = AssemblyRequest(
        node=NODE,
        needs=node.needs,
        system_prompt=node.system_prompt,
        user_prompt=node.request_prompt,
        locale=state["locale"],
        state={k: state.get(k) for k in ("user_message", "messages", "summary_cursor")},
        prompt_vars={
            "message": state["user_message"],
            "headings": headings(current_page),
            "max_sections": MAX_SECTIONS,
        },
    )
    try:
        result = await call_model(ctx, await prepare_call(ctx, request, role=ROLE, step=NODE))
    except TurnError as exc:
        if exc.code != "model_unavailable":
            raise
        span.add_event("report.plan.unavailable")
        return []
    queries = parse_plan(result.text)
    span.set_attribute("report.plan.sections", len(queries))
    log.info("report plan turn=%s sections=%d queries=%s", ctx.turn_id, len(queries), queries)
    return queries
