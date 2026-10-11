"""Ingestion and hybrid retrieval through the real path (phase 04): MinIO, the pipeline,
pgvector, and LiteLLM's `embed` and `rerank` aliases answered by the deterministic test
service (ADR-0143), so ranking is asserted, not only that something came back. Arabic
documents are generated here (G3, G4)."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.attachments.store import collection_for
from app.rag.types import Owner
from tests.fixtures.documents import LEAVE_REGULATION, docx_bytes, pdf_bytes, scanned_pdf_bytes, xlsx_bytes

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


async def _ingest(services, owner, conversation, name, data):
    record = await _upload(services, owner, conversation, name, data)
    outcome = await services.attachments.worker.pipeline.run(record.id)
    return record, outcome, await services.attachments.store.get(owner, conversation, record.id)


async def test_a_visual_order_arabic_pdf_is_indexed_and_the_right_page_ranks_first(services, litellm):
    conversation = str(uuid.uuid4())
    record, outcome, stored = await _ingest(
        services, ALICE, conversation, "لائحة الإجازات.pdf", pdf_bytes(LEAVE_REGULATION, visual=True)
    )
    assert outcome == "indexed" and stored.status == "indexed"
    assert stored.pages == 3 and stored.chunks == 3

    hits = await services.retriever.retrieve(ALICE, collection_for(conversation), "كم مدة إجازة الأمومة؟")
    assert hits[0].page == 2 and "الأمومة" in hits[0].text
    hits = await services.retriever.retrieve(
        ALICE, collection_for(conversation), "اعتبارا من أي عام يعمل بهذه اللائحة؟"
    )
    assert hits[0].page == 3
    # Another user, by the same collection, retrieves nothing (G6).
    assert await services.retriever.retrieve(BOB, collection_for(conversation), "إجازة الأمومة") == []


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
    collection = collection_for(conversation)
    hits = await services.retriever.retrieve(ALICE, collection, "كم يوما يسمح بالعمل عن بعد في الأسبوع؟")
    assert any(h.section and h.section.endswith("العمل عن بعد") for h in hits[:3])
    hits = await services.retriever.retrieve(ALICE, collection, "ما مبلغ بند التدريب في الميزانية؟")
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
    assert await services.attachments.worker.pipeline.run(record.id) == "indexed"
    async with services.db.connection() as conn:
        cursor = await conn.execute(
            "SELECT count(*) AS n FROM agent.rag_chunks WHERE attachment_id = %s", (record.id,)
        )
        assert (await cursor.fetchone())["n"] == first.chunks
    # An indexed attachment is not processed again.
    assert await services.attachments.worker.pipeline.run(record.id) == "skipped"


async def test_a_pending_job_resumes_when_the_worker_starts(services, litellm):
    conversation = str(uuid.uuid4())
    record = await _upload(
        services, ALICE, conversation, "تعميم.txt", "تعميم بشأن ساعات العمل الصيفية.".encode()
    )
    worker = services.attachments.worker
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


async def test_delete_removes_the_record_its_chunks_and_its_object(services, litellm):
    conversation = str(uuid.uuid4())
    record, _, _ = await _ingest(services, ALICE, conversation, "لائحة.pdf", pdf_bytes(LEAVE_REGULATION))
    assert not await services.attachments.delete(BOB, conversation, record.id)
    assert await services.attachments.delete(ALICE, conversation, record.id)
    assert await services.attachments.store.get(ALICE, conversation, record.id) is None
    assert not await services.attachments.objects.exists(record.object_key)
    assert await services.retriever.retrieve(ALICE, collection_for(conversation), "إجازة الأمومة") == []


async def test_the_retention_purge_deletes_expired_files_and_is_idempotent(services, litellm):
    conversation = str(uuid.uuid4())
    record, _, _ = await _ingest(services, ALICE, conversation, "لائحة.pdf", pdf_bytes(LEAVE_REGULATION))
    kept, _, _ = await _ingest(services, ALICE, str(uuid.uuid4()), "أخرى.pdf", pdf_bytes(LEAVE_REGULATION))
    async with services.db.connection() as conn:
        await conn.execute(
            "UPDATE agent.attachments SET expires_at = now() - interval '1 second' WHERE id = %s",
            (record.id,),
        )
    first = await services.attachments.purge_expired()
    assert first["attachments"] >= 1 and first["errors"] == 0
    assert await services.attachments.store.by_id(record.id) is None
    assert not await services.attachments.objects.exists(record.object_key)
    async with services.db.connection() as conn:
        cursor = await conn.execute(
            "SELECT count(*) AS n FROM agent.rag_chunks WHERE attachment_id = %s", (record.id,)
        )
        assert (await cursor.fetchone())["n"] == 0
    again = await services.attachments.purge_expired()
    assert again == {"attachments": 0, "chunks": 0, "objects": 0, "errors": 0}
    assert (await services.attachments.store.by_id(kept.id)).status == "indexed"


async def test_a_turn_moves_the_expiry_of_the_conversations_files(services, litellm):
    conversation = str(uuid.uuid4())
    record, _, _ = await _ingest(services, ALICE, conversation, "لائحة.pdf", pdf_bytes(LEAVE_REGULATION))
    async with services.db.connection() as conn:
        await conn.execute(
            "UPDATE agent.attachments SET expires_at = now() + interval '1 day' WHERE id = %s", (record.id,)
        )
        await conn.execute(
            "UPDATE agent.rag_chunks SET expires_at = now() + interval '1 day' WHERE attachment_id = %s",
            (record.id,),
        )
    await services.attachments.touch(BOB, conversation)  # another user's turn moves nothing
    await services.attachments.touch(ALICE, conversation)
    async with services.db.connection() as conn:
        cursor = await conn.execute(
            "SELECT (SELECT expires_at FROM agent.attachments WHERE id = %(id)s) > %(soon)s AS a, "
            "(SELECT min(expires_at) FROM agent.rag_chunks WHERE attachment_id = %(id)s) > %(soon)s AS c",
            {"id": record.id, "soon": datetime.now(UTC) + timedelta(days=29)},
        )
        row = await cursor.fetchone()
    assert row["a"] and row["c"]
