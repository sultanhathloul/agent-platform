"""ingest_attachments (README §5.2, phase 04 A1): loads the conversation's attachments
into AgentState after the ownership check, and only reads their status: ingestion runs in
the background worker, and this node never waits for it (ADR-0130). An attachment id the
turn names that the caller does not have in this conversation (another user's included)
ends the turn with `attachment_not_found` (G6). It runs before INPUT, so INPUT checks the
names of the files sent with the turn (owner decision S2)."""

from langgraph.runtime import Runtime
from opentelemetry import trace

from app.errors import TurnError
from app.graph.deps import TurnContext
from app.graph.nodes import traced
from app.graph.state import AgentState, Attachment
from app.rag.types import Owner


@traced("ingest_attachments")
async def ingest_attachments(state: AgentState, runtime: Runtime[TurnContext]) -> dict:
    ctx = runtime.context
    principal = ctx.principal
    owner = Owner(tenant_id=principal.tenant_id, user_id=principal.user_id)
    conversation_id = state["conversation_id"]
    service = ctx.services.attachments
    # Read at once under one bound; a backend that cannot answer leaves the last known status (ADR-0233).
    records = await service.with_status(owner, conversation_id, on_audit=ctx.tool_audit.append)
    known = {record.id for record in records}
    named = list(state.get("turn_attachment_ids") or [])
    span = trace.get_current_span()
    span.set_attribute("attachments.turn", len(named))
    if any(attachment_id not in known for attachment_id in named):
        raise TurnError(
            "attachment_not_found", detail="the turn names an attachment the caller does not have"
        )
    attachments = [
        Attachment(id=r.id, name=r.name, mime=r.mime, size=r.size, collection=r.collection, status=r.status)
        for r in records
    ]
    for status in ("pending", "indexed", "failed"):
        span.set_attribute(f"attachments.{status}", sum(a["status"] == status for a in attachments))
    return {"attachments": attachments}
