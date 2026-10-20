"""Ingestion and hybrid retrieval through the real path (phase 04): MinIO, the pipeline,
pgvector, and LiteLLM's `embed` and `rerank` aliases answered by the deterministic test
service (ADR-0143), so ranking is asserted, not only that something came back. Arabic
documents are generated here (G3, G4)."""

import asyncio
import uuid

import pytest

from app.rag.backend import DocumentScope
from app.rag.types import Owner
from tests.fixtures.documents import LEAVE_REGULATION, docx_bytes, pdf_bytes, scanned_pdf_bytes, xlsx_bytes
from tests.fixtures.ingestion import ingestion

pytestmark = pytest.mark.integration

ALICE = Owner(tenant_id="default", user_id="u-alice-ingest")
BOB = Owner(tenant_id="default", user_id="u-bob-ingest")
MIMES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "txt": "text/plain",
}


async def _upload(services, owner: Owner, conversation: str, name: str, data: bytes):
    kind = name.rsplit(".", 1)[1]
    key = f"test/{conversation}/{uuid.uuid4()}"
    await services.attachments.objects.put(key, data, MIMES[kind])
    return await services.attachments.register(
        owner, conversation, name=name, mime=MIMES[kind], size=len(data), object_key=key
    )


async def _search(services, owner, conversation, question):
    """The conversation's chunks through the DocumentBackend, as the docs tool reaches them (C6)."""
    scope = DocumentScope.session(
        tenant_id=owner.tenant_id, user_id=owner.user_id, conversation_id=conversation
    )
    return await services.documents.search(question, scope, 8)


async def _ingest(services, owner, conversation, name, data):
    record = await _upload(services, owner, conversation, name, data)
    outcome = await ingestion(services).pipeline.run(record.id)
    return record, outcome, await services.attachments.store.get(owner, conversation, record.id)


async def test_a_visual_order_arabic_pdf_is_indexed_and_the_right_page_ranks_first(services, litellm):
    conversation = str(uuid.uuid4())
    record, outcome, stored = await _ingest(
        services, ALICE, conversation, "لائحة الإجازات.pdf", pdf_bytes(LEAVE_REGULATION, visual=True)
    )
    assert outcome == "indexed" and stored.status == "indexed"
    assert stored.pages == 3 and stored.chunks == 3

    hits = await _search(services, ALICE, conversation, "كم مدة إجازة الأمومة؟")
    assert hits[0].page == 2 and "الأمومة" in hits[0].text
    hits = await _search(services, ALICE, conversation, "اعتبارا من أي عام يعمل بهذه اللائحة؟")
    assert hits[0].page == 3
    # Another user, by the same collection, retrieves nothing (G6).
    assert await _search(services, BOB, conversation, "إجازة الأمومة") == []


async def test_docx_sections_and_xlsx_rows_are_retrieved_in_the_top_three(services, litellm):
    conversation = str(uuid.uuid4())
    policy = docx_bytes(
        [
            ("دليل السياسات الداخلية", 0),
            ("الباب الأول: الدوام", 1),
            ("يبدأ الدوام الرسمي الساعة السابعة والنصف صباحا وينتهي الساعة الثالثة.", "p"),
            ("الباب الثاني: العمل عن بعد", 1),
            ("يسمح بالعمل عن بعد يومين في الأسبوع بموافقة المدير المباشر.", "p"),
            ("الباب الثالث: المكافآت", 1),
            ("تصرف مكافأة الأداء السنوية في شهر محرم.", "p"),
        ]
    )
    sheet = xlsx_bytes(
        {"الميزانية": [["البند", "المبلغ"], ["التدريب", 120000], ["السفر", 45000], ["الضيافة", 9000]]}
    )
    for name, data in (("السياسات.docx", policy), ("الميزانية.xlsx", sheet)):
        _, outcome, _ = await _ingest(services, ALICE, conversation, name, data)
        assert outcome == "indexed"
    hits = await _search(services, ALICE, conversation, "كم يوما يسمح بالعمل عن بعد في الأسبوع؟")
    assert any(h.section and h.section.endswith("العمل عن بعد") for h in hits[:3])
    hits = await _search(services, ALICE, conversation, "ما مبلغ بند التدريب في الميزانية؟")
    assert any("التدريب" in h.text and h.section == "الميزانية" for h in hits[:3])


async def test_a_scanned_pdf_ends_failed_with_scanned_unsupported(services, litellm):
    conversation = str(uuid.uuid4())
    _, outcome, stored = await _ingest(services, ALICE, conversation, "مسح ضوئي.pdf", scanned_pdf_bytes(2))
    assert outcome == "failed"
    assert stored.status == "failed" and stored.reason == "scanned_unsupported" and stored.pages == 2


async def test_a_file_whose_bytes_do_not_match_its_extension_fails(services, litellm):
    conversation = str(uuid.uuid4())
    _, outcome, stored = await _ingest(services, ALICE, conversation, "ليس ملف.pdf", "نص عادي".encode())
    assert outcome == "failed" and stored.reason == "unsupported_type"


async def test_ingestion_is_idempotent_by_attachment_id(services, litellm):
    conversation = str(uuid.uuid4())
    record, _, first = await _ingest(services, ALICE, conversation, "لائحة.pdf", pdf_bytes(LEAVE_REGULATION))
    async with services.db.connection() as conn:
        await conn.execute("UPDATE agent.attachments SET status = 'pending' WHERE id = %s", (record.id,))
    assert await ingestion(services).pipeline.run(record.id) == "indexed"
    async with services.db.connection() as conn:
        cursor = await conn.execute(
            "SELECT count(*) AS n FROM agent.rag_chunks WHERE attachment_id = %s", (record.id,)
        )
        assert (await cursor.fetchone())["n"] == first.chunks
    # An indexed attachment is not processed again.
    assert await ingestion(services).pipeline.run(record.id) == "skipped"


async def test_a_pending_job_resumes_when_the_worker_starts(services, litellm):
    conversation = str(uuid.uuid4())
    record = await _upload(
        services, ALICE, conversation, "تعميم.txt", "تعميم بشأن ساعات العمل الصيفية.".encode()
    )
    # Other tests leave pending rows whose objects never existed; settle them so the queue
    # holds only this job.
    async with services.db.connection() as conn:
        await conn.execute(
            "UPDATE agent.attachments SET status = 'failed', reason = 'ingest_error' "
            "WHERE status = 'pending' AND id <> %s",
            (record.id,),
        )
    worker = ingestion(services).worker
    await worker.start()  # puts every pending attachment back on the queue
    try:
        for _ in range(100):
            stored = await services.attachments.store.get(ALICE, conversation, record.id)
            if stored.status != "pending":
                break
            await asyncio.sleep(0.1)
    finally:
        await worker.stop()
    assert stored.status == "indexed"
