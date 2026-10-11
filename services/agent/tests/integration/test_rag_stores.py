"""The attachment store and the VectorStore (phase 04, G2, G6): isolation first. A user
never lists, reads, deletes or retrieves another user's attachment or chunk, by any id,
in any collection; the file limit is atomic; expired rows are never returned."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.attachments.store import AttachmentStore, LimitReached, collection_for
from app.rag.types import Chunk, Owner
from app.rag.vectorstore import PgVectorStore

pytestmark = pytest.mark.integration

ALICE = Owner(tenant_id="default", user_id="u-alice-rag")
BOB = Owner(tenant_id="default", user_id="u-bob-rag")
ALICE_T2 = Owner(tenant_id="t2", user_id="u-alice-rag")  # same user id, another tenant


@pytest.fixture
def store(services) -> AttachmentStore:
    return AttachmentStore(services.db)


@pytest.fixture
def vectors(services) -> PgVectorStore:
    return PgVectorStore(services.db)


async def _register(store: AttachmentStore, owner: Owner, conversation: str, name: str = "لائحة.pdf"):
    return await store.register(
        owner,
        conversation,
        name=name,
        mime="application/pdf",
        kind="pdf",
        size=1234,
        object_key=f"{conversation}/{uuid.uuid4()}",
        max_files=10,
        retention_days=30,
    )


async def test_attachments_are_invisible_to_another_user_and_tenant_by_any_id(store, services):
    conversation = str(uuid.uuid4())
    record = await _register(store, ALICE, conversation)
    assert record.status == "pending" and record.collection == collection_for(conversation)

    for intruder in (BOB, ALICE_T2):
        assert await store.list_for(intruder, conversation) == []
        assert await store.get(intruder, conversation, record.id) is None
        async with services.db.connection() as conn:
            assert not await store.delete(intruder, conversation, record.id, conn=conn)
    # The owner, with the right id but another conversation, gets nothing either.
    assert await store.get(ALICE, str(uuid.uuid4()), record.id) is None
    assert [r.id for r in await store.list_for(ALICE, conversation)] == [record.id]
    assert (await store.get(ALICE, conversation, record.id)).name == "لائحة.pdf"


async def test_the_file_limit_holds_under_concurrent_uploads(store):
    conversation = str(uuid.uuid4())

    async def one(index: int):
        try:
            return await store.register(
                ALICE,
                conversation,
                name=f"f{index}.txt",
                mime="text/plain",
                kind="txt",
                size=1,
                object_key=f"k{index}",
                max_files=3,
                retention_days=30,
            )
        except LimitReached:
            return None

    results = await asyncio.gather(*(one(i) for i in range(8)))
    assert sum(r is not None for r in results) == 3
    assert len(await store.list_for(ALICE, conversation)) == 3


async def test_expired_attachments_are_not_listed_and_are_found_by_the_purge(store, services):
    conversation = str(uuid.uuid4())
    record = await _register(store, ALICE, conversation)
    async with services.db.connection() as conn:
        await conn.execute(
            "UPDATE agent.attachments SET expires_at = now() - interval '1 second' WHERE id = %s",
            (record.id,),
        )
    assert await store.list_for(ALICE, conversation) == []
    assert await store.get(ALICE, conversation, record.id) is None
    assert record.id in [r.id for r in await store.expired()]


async def test_touch_moves_only_the_owners_attachments(store):
    conversation = str(uuid.uuid4())
    mine = await _register(store, ALICE, conversation)
    later = datetime.now(UTC) + timedelta(days=60)
    assert await store.touch(BOB, conversation, later) == 0
    assert await store.touch(ALICE, conversation, later) == 1
    assert (await store.get(ALICE, conversation, mine.id)).expires_at > mine.expires_at


def _chunks(*texts: str) -> list[Chunk]:
    return [Chunk(ord=i, text=t, page=i + 1, tokens=len(t.split())) for i, t in enumerate(texts)]


async def test_chunks_are_retrieved_only_by_their_owner_and_collection(vectors):
    collection = collection_for(str(uuid.uuid4()))
    document = str(uuid.uuid4())
    chunks = _chunks("تمنح الإجازة السنوية ثلاثين يومًا.", "يعمل الموظف ثماني ساعات يوميًا.")
    vector = [0.1, 0.2, 0.3, 0.4]
    expires = datetime.now(UTC) + timedelta(days=30)
    assert (
        await vectors.replace_document(
            ALICE, collection, document, chunks, [vector, vector], expires_at=expires
        )
        == 2
    )

    for intruder in (BOB, ALICE_T2):
        assert await vectors.vector_search(intruder, collection, vector, limit=10) == []
        assert await vectors.text_search(intruder, collection, ["اجازه"], limit=10) == []
        assert await vectors.delete_document(intruder, collection, document) == 0
    other_collection = collection_for(str(uuid.uuid4()))
    assert await vectors.vector_search(ALICE, other_collection, vector, limit=10) == []
    assert len(await vectors.vector_search(ALICE, collection, vector, limit=10)) == 2
    # A document filter cannot reach outside the owner's collection either.
    assert await vectors.vector_search(BOB, collection, vector, limit=10, document_ids=[document]) == []


async def test_text_search_matches_arabic_forms_deterministically(vectors):
    collection = collection_for(str(uuid.uuid4()))
    document = str(uuid.uuid4())
    chunks = _chunks(
        "تُمنَح الإجـــازة السنويّة للموظف ثلاثين يومًا.",  # tashkeel, tatweel, alef with hamza
        "ساعات العمل الرسمية ثماني ساعات.",
    )
    await vectors.replace_document(
        ALICE, collection, document, chunks, [[1.0, 0.0], [0.0, 1.0]], expires_at=None
    )
    from app.text.arabic import search_terms

    hits = await vectors.text_search(ALICE, collection, search_terms("كم مدة الاجازه؟"), limit=5)
    assert [h.ord for h in hits] == [0]
    hits = await vectors.text_search(ALICE, collection, search_terms("والإجازة"), limit=5)
    assert [h.ord for h in hits] == [0]


async def test_replace_document_is_idempotent_and_expired_chunks_are_never_returned(vectors, services):
    collection = collection_for(str(uuid.uuid4()))
    document = str(uuid.uuid4())
    vector = [0.5, 0.5]
    await vectors.replace_document(
        ALICE, collection, document, _chunks("أ ب ج", "د هـ و"), [vector] * 2, expires_at=None
    )
    await vectors.replace_document(
        ALICE, collection, document, _chunks("أ ب ج", "د هـ و"), [vector] * 2, expires_at=None
    )
    assert len(await vectors.vector_search(ALICE, collection, vector, limit=10)) == 2
    past = datetime.now(UTC) - timedelta(seconds=1)
    assert await vectors.touch(ALICE, collection, past) == 2
    assert await vectors.vector_search(ALICE, collection, vector, limit=10) == []
    assert await vectors.purge_document(document) == 2
