"""No deletion in the product (owner decision S4). Operations delete cleanly: a context
package names the attachments whose chunks it holds, and deleting an attachment record takes
its chunks (tests/integration/test_rag_stores.py). The orphan sweeper removes an upload's
object that no attachment record names once it is old enough, and never one a record names
or one still being registered."""

import uuid
from datetime import timedelta

import pytest
from tests.fixtures.backends import Harness
from tests.fixtures.documents import LEAVE_REGULATION
from tests.fixtures.replace import replacing

from app.api.runner import TurnRunner
from app.api.schemas import TurnRequest
from app.attachments.sweeper import OrphanSweeper
from app.identity import Principal
from app.rag.backend import DocumentScope

pytestmark = pytest.mark.integration


async def test_a_context_package_names_the_attachments_its_chunks_came_from(debug_services, litellm):
    harness = Harness("internal", debug_services)
    principal = Principal(user_id=f"u-s4-{uuid.uuid4().hex[:6]}", permissions=("cap:general", "cap:docs"))
    conversation = str(uuid.uuid4())
    scope = DocumentScope(
        tenant_id=principal.tenant_id, user_id=principal.user_id, conversation_id=conversation
    )
    document = await harness.upload(scope, "لائحة.pdf", ["\n".join(page) for page in LEAVE_REGULATION])
    assert await harness.ingest(document) == "indexed"
    replaced, _ = replacing(debug_services, {"reasoning": "تمنح إجازة الأمومة عشرة أسابيع براتب كامل."})
    request = TurnRequest(
        conversation_id=conversation,
        principal=principal,
        message="كم مدة إجازة الأمومة؟",
        attachment_ids=[document],
        locale="ar",
    )
    events = [e async for e in TurnRunner(replaced).stream(request, "req-s4")]
    assert events[-1].type == "done"
    async with debug_services.db.connection() as conn:
        cursor = await conn.execute(
            "SELECT node, attachment_ids::text[] AS ids FROM agent.context_packages "
            "WHERE conversation_id = %s",
            (conversation,),
        )
        rows = {row["node"]: row["ids"] for row in await cursor.fetchall()}
    assert rows["answer"] == [document]


async def test_the_sweeper_removes_only_old_objects_no_record_names(services):
    from app.attachments.objects import ObjectStore

    objects, store = services.attachments.objects, services.attachments.store
    settings = services.settings
    # The sweeper's own MinIO user lists and removes; the gateway's (objects) stores (S7).
    sweeping = ObjectStore(
        settings.minio_endpoint,
        settings.minio_sweeper_access_key,
        settings.minio_sweeper_secret_key,
        settings.uploads_bucket,
    )
    conversation = str(uuid.uuid4())
    orphan = f"test/{conversation}/{uuid.uuid4()}"
    named = f"test/{conversation}/{uuid.uuid4()}"
    await objects.put(orphan, b"%PDF-1.4 orphan", "application/pdf")
    await objects.put(named, b"%PDF-1.4 named", "application/pdf")
    owner = Principal(user_id=f"u-s4-{uuid.uuid4().hex[:6]}")
    from app.rag.types import Owner

    await store.register(
        Owner(tenant_id=owner.tenant_id, user_id=owner.user_id),
        conversation,
        name="مسجل.pdf",
        mime="application/pdf",
        kind="pdf",
        size=14,
        object_key=named,
        max_files=10,
    )

    # Too young: an upload may still be registering, so nothing is removed.
    young = await OrphanSweeper(sweeping, store, min_age=timedelta(hours=1)).sweep()
    assert await objects.exists(orphan) and young["removed"] == 0
    # Old enough: the orphan goes, the named object stays.
    await OrphanSweeper(sweeping, store, min_age=timedelta(seconds=0)).sweep()
    assert not await objects.exists(orphan)
    assert await objects.exists(named)


async def test_each_service_user_can_do_only_its_own_part(services):
    """S7: the gateway's user stores, reads and discards; the ingest user only reads; the
    sweeper only lists and removes. No service holds MinIO's root credentials."""
    from app.attachments.objects import ObjectStore, StorageUnavailable

    settings = services.settings

    def store(key: str, secret: str) -> ObjectStore:
        return ObjectStore(settings.minio_endpoint, key, secret, settings.uploads_bucket)

    gateway = services.attachments.objects
    sweeper = store(settings.minio_sweeper_access_key, settings.minio_sweeper_secret_key)
    key = f"test/{uuid.uuid4()}/{uuid.uuid4()}"
    await gateway.put(key, b"%PDF-1.4 user test", "application/pdf")
    assert await gateway.get(key) == b"%PDF-1.4 user test"
    with pytest.raises(StorageUnavailable):
        await sweeper.get(key)  # the sweeper cannot read a document
    with pytest.raises(Exception):  # noqa: B017 (minio raises S3Error on a refused put)
        await sweeper.put(f"{key}-2", b"x", "application/pdf")
    assert key in await sweeper.keys_older_than(timedelta(seconds=0))
    with pytest.raises(StorageUnavailable):
        await gateway.keys_older_than(timedelta(seconds=0))  # the gateway's user cannot list
    assert await sweeper.delete(key)
    assert not await gateway.exists(key)
