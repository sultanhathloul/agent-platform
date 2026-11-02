"""The conversation's report index for every turn's context (report library PR, owner decision 1, ADR-0430).

Read from the report store once per turn, only in a conversation whose state holds a report (no query
otherwise), and cached on the turn: the answer, a report turn and a text turn all list the same reports.
Each report at its latest version, the most recently changed first, at most config/library.yaml
index.max_reports, each digest cut to index.digest_chars. A store that cannot answer leaves the index out
for this turn (recorded on the span); the turn goes on."""

import logging

from opentelemetry import trace

from app.artifacts.library import digest

log = logging.getLogger(__name__)


def has_reports(state: dict) -> bool:
    return any(a.get("kind") == "report" for a in state.get("artifacts") or [])


async def report_index(state: dict, ctx) -> list[dict]:
    if ctx.report_index is not None:
        return ctx.report_index
    services = ctx.services
    config = services.library
    if not has_reports(state) or services.reports is None or config is None:
        ctx.report_index = []
        return ctx.report_index
    principal = ctx.principal
    try:
        records = await services.reports.in_conversation_latest(
            principal.tenant_id, principal.user_id, state["conversation_id"], config.index.max_reports
        )
    except Exception as exc:  # the index is context, never a reason to fail the turn
        trace.get_current_span().add_event("report_index.unavailable", {"error": type(exc).__name__})
        log.warning("report index unavailable for turn %s: %s", ctx.turn_id, type(exc).__name__)
        records = []
    locale = state.get("locale") or "ar"
    ctx.report_index = [
        {
            "report_id": r.report_id,
            "title": r.title,
            "version": r.version,
            "digest": digest(r.report_id, r.title, r.version, r.spec, locale).text(config.index.digest_chars),
        }
        for r in records
    ]
    trace.get_current_span().set_attribute("report_index.reports", len(ctx.report_index))
    return ctx.report_index


def marker_digest(report_id: str, title: str, version: int, spec: dict | None, locale: str, limit: int) -> str:
    """The digest a report reply keeps beside it in the history (the window's marker)."""
    return digest(report_id, title, version, spec, locale).text(limit)
