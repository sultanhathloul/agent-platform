"""Tool output stays out of traces (owner decision S5): an llm_judge call at POST_TOOL, whose
text is document chunks or their metadata, carries no messages and no output in its trace
and records no context package; an extraction error is logged and spanned by its type and its
reason code only, never by a library's message, which can quote the document."""

import json
import logging
import uuid

import pytest

from app.api.runner import TurnRunner
from app.api.schemas import TurnRequest
from app.guardrails.checkpoints import Checkpoint
from app.guardrails.schema import Policy
from app.identity import Principal
from app.rag.backend import DocumentScope
from tests.fixtures.backends import Harness
from tests.fixtures.ingestion import ingestion
from tests.fixtures.replace import replacing

pytestmark = pytest.mark.integration

PAGE = "تبدأ ساعات العمل الصيفية الساعة السابعة صباحا في مقر هيئة الواحة."
VERDICT = json.dumps({"violates": False, "confidence": 0.9, "category": "none"})


def _span_text(spans) -> str:
    return "\n".join(
        str(value)
        for span in spans.get_finished_spans()
        for value in [
            *(span.attributes or {}).values(),
            *(a for e in span.events for a in e.attributes.values()),
        ]
    )


async def test_a_judge_at_post_tool_is_content_free_and_records_no_package(debug_services, spans, litellm):
    role = f"j-{uuid.uuid4().hex[:8]}"
    pid = f"judge-post-tool-{uuid.uuid4().hex[:8]}"
    await debug_services.policies.create(
        Policy(
            id=pid,
            name_ar="سياسة اختبار",
            name_en="judge on tool output",
            scope=f"role:{role}",
            checkpoints=[Checkpoint.POST_TOOL],
            checker="llm_judge",
            config={"topic_description": "أي موضوع سري"},
            action="log_only",
        ),
        actor="test",
    )
    try:
        harness = Harness("internal", debug_services)
        principal = Principal(
            user_id=f"u-s5-{uuid.uuid4().hex[:6]}",
            permissions=("cap:general", "cap:docs"),
            policy_roles=(f"policy:{role}",),
        )
        conversation = str(uuid.uuid4())
        scope = DocumentScope.session(
            tenant_id=principal.tenant_id, user_id=principal.user_id, conversation_id=conversation
        )
        document = await harness.upload(scope, "تعميم.md", [PAGE], sections=["ساعات العمل"])
        assert await harness.ingest(document) == "indexed"
        replaced, gateway = replacing(debug_services, {"judge": VERDICT, "reasoning": "في السابعة."})
        spans.clear()
        request = TurnRequest(
            conversation_id=conversation,
            principal=principal,
            message="متى تبدأ ساعات العمل الصيفية؟",
            attachment_ids=[document],
            locale="ar",
        )
        events = [e async for e in TurnRunner(replaced).stream(request, "req-s5")]
        assert events[-1].type == "done"
        assert gateway.calls.count("judge") >= 1  # the search's metadata and the fetched chunk
        judges = [
            s
            for s in spans.get_finished_spans()
            if s.name == "model.call" and s.attributes.get("model.role") == "judge"
        ]
        assert judges
        for call in judges:
            assert "langfuse.observation.input" not in call.attributes
            assert "langfuse.observation.output" not in call.attributes
            assert call.attributes["model.content_free"] is True
        assert "مقر هيئة الواحة" not in _span_text(spans)
        async with debug_services.db.connection() as conn:
            cursor = await conn.execute(
                "SELECT node FROM agent.context_packages WHERE turn_id = %s", (events[-1].turn_id,)
            )
            nodes = {row["node"] for row in await cursor.fetchall()}
        assert "answer" in nodes and "guard_judge" not in nodes
    finally:
        await debug_services.policies.set_enabled(pid, False, actor="test")


SECRET = "SECRET-LIBRARY-MESSAGE quoting the document: رقم الهوية 1234567897"


def test_the_parsing_child_sends_a_defects_type_never_its_message(config_dir, monkeypatch):
    from app.rag.config import load_docs_config
    from app.rag.ingest import sandbox

    def explode(kind, data, config):
        raise ValueError(SECRET)

    monkeypatch.setattr(sandbox, "extract", explode)
    assert sandbox.outcome("md", b"# x", load_docs_config(config_dir)) == ("error", "ValueError", None)


async def test_an_extraction_error_is_reported_by_type_and_code_only(
    services, spans, litellm, caplog, monkeypatch
):
    from app.rag.ingest import pipeline as pipeline_module

    def explode(kind, data, config):
        raise ValueError(SECRET)

    monkeypatch.setattr(pipeline_module, "run_sandboxed", explode)
    harness = Harness("internal", services)
    scope = DocumentScope.session(
        tenant_id="default", user_id=f"u-s5-{uuid.uuid4().hex[:6]}", conversation_id=str(uuid.uuid4())
    )
    document = await harness.upload(scope, "ملف.md", ["نص"], sections=["عنوان"])
    spans.clear()
    with caplog.at_level(logging.DEBUG):
        outcome = await ingestion(services).worker._job(document)
    assert outcome == "failed"
    status = await services.documents.status(document, scope)
    assert (status.status, status.reason) == ("failed", "ingest_error")
    assert "SECRET-LIBRARY-MESSAGE" not in caplog.text and "ValueError" in caplog.text
    assert "SECRET-LIBRARY-MESSAGE" not in _span_text(spans)
    [ingest] = [s for s in spans.get_finished_spans() if s.name == "rag.ingest"]
    assert ingest.attributes["ingest.error_type"] == "ValueError" and not ingest.events
