"""The attachment store and the VectorStore (phase 04, G2, G6): isolation first. A user
never lists, reads or retrieves another user's attachment or chunk, by any id, in any
collection; the file limit is atomic. The product deletes nothing (owner decision S4); a chunk
needs its attachment record, and deleting the record (an operations task) takes its chunks."""

import asyncio
import uuid

import psycopg
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
    )


async def test_attachments_are_invisible_to_another_user_and_tenant_by_any_id(store, services):
    conversation = str(uuid.uuid4())
    record = await _register(store, ALICE, conversation)
    assert record.status == "pending" and record.collection == collection_for(conversation)

    for intruder in (BOB, ALICE_T2):
        assert await store.list_for(intruder, conversation) == []
        assert await store.get(intruder, conversation, record.id) is None
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
            )
        except LimitReached:
            return None

    results = await asyncio.gather(*(one(i) for i in range(8)))
    assert sum(r is not None for r in results) == 3
    assert len(await store.list_for(ALICE, conversation)) == 3


def _chunks(*texts: str) -> list[Chunk]:
    return [Chunk(ord=i, text=t, page=i + 1, tokens=len(t.split())) for i, t in enumerate(texts)]


async def _document(store: AttachmentStore, owner: Owner = ALICE) -> tuple[str, str]:
    """(collection, document id) of a new attachment record: a chunk needs its record (S4)."""
    conversation = str(uuid.uuid4())
    record = await _register(store, owner, conversation)
    return collection_for(conversation), record.id


async def test_chunks_are_retrieved_only_by_their_owner_and_collection(vectors, store):
    collection, document = await _document(store)
    chunks = _chunks("تمنح الإجازة السنوية ثلاثين يومًا.", "يعمل الموظف ثماني ساعات يوميًا.")
    vector = [0.1, 0.2, 0.3, 0.4]
    assert await vectors.replace_document(ALICE, collection, document, chunks, [vector, vector]) == 2

    for intruder in (BOB, ALICE_T2):
        assert await vectors.vector_search(intruder, collection, vector, limit=10) == []
        assert await vectors.text_search(intruder, collection, ["اجازه"], limit=10) == []
    other_collection = collection_for(str(uuid.uuid4()))
    assert await vectors.vector_search(ALICE, other_collection, vector, limit=10) == []
    assert len(await vectors.vector_search(ALICE, collection, vector, limit=10)) == 2
    # A document filter cannot reach outside the owner's collection either.
    assert await vectors.vector_search(BOB, collection, vector, limit=10, document_ids=[document]) == []


async def test_text_search_matches_arabic_forms_deterministically(vectors, store):
    collection, document = await _document(store)
    chunks = _chunks(
        "تُمنَح الإجـــازة السنويّة للموظف ثلاثين يومًا.",  # tashkeel, tatweel, alef with hamza
        "ساعات العمل الرسمية ثماني ساعات.",
    )
    await vectors.replace_document(ALICE, collection, document, chunks, [[1.0, 0.0], [0.0, 1.0]])
    from app.text.arabic import search_terms

    hits = await vectors.text_search(ALICE, collection, search_terms("كم مدة الاجازه؟"), limit=5)
    assert [h.ord for h in hits] == [0]
    hits = await vectors.text_search(ALICE, collection, search_terms("والإجازة"), limit=5)
    assert [h.ord for h in hits] == [0]


async def test_replace_document_is_idempotent(vectors, store):
    collection, document = await _document(store)
    vector = [0.5, 0.5]
    for _ in range(2):
        await vectors.replace_document(ALICE, collection, document, _chunks("أ ب ج", "د هـ و"), [vector] * 2)
    assert len(await vectors.vector_search(ALICE, collection, vector, limit=10)) == 2


async def test_fetch_returns_only_the_owners_chunks_of_the_collection(vectors, store):
    collection, document = await _document(store)
    await vectors.replace_document(ALICE, collection, document, _chunks("أ ب ج"), [[1.0, 0.0]])
    [hit] = await vectors.vector_search(ALICE, collection, [1.0, 0.0], limit=1)
    assert await vectors.fetch(ALICE, collection, [hit.id, "not-a-uuid"]) == {hit.id: "أ ب ج"}
    assert await vectors.fetch(BOB, collection, [hit.id]) == {}
    assert await vectors.fetch(ALICE, collection_for(str(uuid.uuid4())), [hit.id]) == {}


async def test_a_chunk_needs_its_attachment_record(vectors):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        await vectors.replace_document(
            ALICE, collection_for(str(uuid.uuid4())), str(uuid.uuid4()), _chunks("نص"), [[1.0]]
        )


async def test_deleting_an_attachment_record_deletes_its_chunks(vectors, store, services):
    """The operations step of PROGRESS (owner decision S4): one DELETE, the chunks go with it."""
    collection, document = await _document(store)
    await vectors.replace_document(ALICE, collection, document, _chunks("أ", "ب"), [[1.0, 0.0]] * 2)
    async with services.db.connection() as conn:
        await conn.execute("DELETE FROM agent.attachments WHERE id = %s", (document,))
        cursor = await conn.execute(
            "SELECT count(*) AS n FROM agent.rag_chunks WHERE attachment_id = %s", (document,)
        )
        assert (await cursor.fetchone())["n"] == 0
