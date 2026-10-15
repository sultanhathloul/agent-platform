"""Document collections (phase 05, I1, ADR-0202). Isolation first (G6): the broker builds a tool's
collections from the verified principal and the turn, so a knowledge base is reached only when
the principal holds `kb:{slug}:read`, the turn selected it, and the calling capability's manifest
declares the `kb` kind; another tenant never reaches it. The answer step fetches a source's text
only from a collection the capability that found it may read this turn, whatever the source
names. Then G9: a conversation persisted before this change reopens with its citations, and a
docs turn now persists citations of the same shape, with no backend or collection in them."""

import dataclasses
import uuid

import pytest

from app.api.runner import TurnRunner
from app.api.schemas import TurnRequest
from app.graph.deps import TurnContext
from app.graph.evidence import load_evidence
from app.identity import Principal
from app.rag.backend import DocumentScope
from app.tools.broker import ToolBroker
from tests.fixtures.backends import Harness
from tests.fixtures.http import post_turn

pytestmark = pytest.mark.integration

MOE = ["تعميم وزاري: يبدأ العام الدراسي في الأسبوع الأول من شهر سبتمبر لجميع المراحل."]
QUESTION = "متى يبدأ العام الدراسي؟"


def _principal(*permissions: str, tenant: str = "default") -> Principal:
    return Principal(
        user_id=f"u-coll-{uuid.uuid4().hex[:8]}", tenant_id=tenant, permissions=("cap:docs", *permissions)
    )


def _broker(services) -> ToolBroker:
    """The session's registry and guard, with a test-only caller `kbreader` declaring docs_search
    and both kinds, beside the real `docs` (session only). No core file names either."""
    return ToolBroker(
        services.tools,
        services.guard,
        {"docs": ["docs_search"], "kbreader": ["docs_search"]},
        {"docs": ["session"], "kbreader": ["session", "kb"]},
    )


@pytest.fixture
def harness(services, litellm) -> Harness:
    return Harness("fake", services)


async def _search(broker, harness, caller, principal, selected):
    return await broker.execute(
        "docs_search",
        {"query": QUESTION},
        caller=caller,
        principal=principal,
        conversation_id=str(uuid.uuid4()),
        selected_kbs=selected,
        documents=harness.backend,
    )


async def test_a_knowledge_base_is_reached_only_when_permitted_selected_and_declared(harness):
    document = harness.fake.add_kb("default", "moe", "تعميم.pdf", MOE)
    broker = _broker(harness.services)

    found = await _search(broker, harness, "kbreader", _principal("kb:moe:read"), ("moe",))
    assert found.status == "ok" and [s["attachment_id"] for s in found.sources] == [document]
    assert found.sources[0]["collection"] == "kb_moe" and found.sources[0]["backend"] == "fake"

    for caller, principal, selected in [
        ("kbreader", _principal(), ("moe",)),  # selected, not permitted
        ("kbreader", _principal("kb:moe:read"), ()),  # permitted, not selected
        ("kbreader", _principal("kb:hr:read"), ("moe", "hr")),  # another knowledge base's permission
        ("kbreader", _principal("kb:moe:read", tenant="t2"), ("moe",)),  # another tenant's moe
        ("docs", _principal("kb:moe:read"), ("moe",)),  # docs declares [session] only
    ]:
        result = await _search(broker, harness, caller, principal, selected)
        assert (result.status, result.sources) == ("ok", []), (caller, principal.permissions, selected)


async def test_a_backend_returns_nothing_outside_the_scope_by_any_reference(harness):
    document = harness.fake.add_kb("default", "moe", "تعميم.pdf", MOE)
    owner = _principal("kb:moe:read")
    inside = DocumentScope(tenant_id="default", user_id=owner.user_id, collections=("kb_moe",))
    [hit] = await harness.backend.search(QUESTION, inside, 8)
    for outside in [
        DocumentScope(tenant_id="default", user_id=owner.user_id),
        DocumentScope.session(tenant_id="default", user_id=owner.user_id, conversation_id=str(uuid.uuid4())),
        DocumentScope(tenant_id="t2", user_id=owner.user_id, collections=("kb_moe",)),
        DocumentScope(tenant_id="default", user_id=owner.user_id, collections=("kb_hr",)),
    ]:
        assert await harness.backend.search(QUESTION, outside, 8) == []
        assert await harness.backend.fetch([hit.ref], outside) == {}
        assert await harness.backend.status(document, outside) is None
    assert await harness.backend.fetch([hit.ref], inside) == {hit.ref: hit.text}


async def test_the_answer_step_fetches_only_from_a_collection_the_capability_may_read(harness):
    """A source naming a knowledge base collection, from a capability whose scope this turn does
    not hold it, is never fetched: by its collection, by no collection, or by another backend."""
    harness.fake.add_kb("default", "moe", "تعميم.pdf", MOE)
    principal = _principal("kb:moe:read")
    [hit] = await harness.backend.search(
        QUESTION, DocumentScope(tenant_id="default", user_id=principal.user_id, collections=("kb_moe",)), 8
    )
    services = dataclasses.replace(harness.services, broker=_broker(harness.services))
    ctx = TurnContext(services=services, principal=principal, request_id="r", turn_id=uuid.uuid4().hex)
    base = {"id": hit.ref, "ref": hit.ref, "title": "تعميم.pdf", "kind": "doc", "page": 1}
    for source in [
        {**base, "backend": "fake", "collection": "kb_moe"},  # docs may not read a kb collection
        {**base, "backend": "fake"},  # no collection: docs' own scope, the session only
        {**base, "backend": "elsewhere", "collection": "kb_moe"},  # a backend that does not exist
    ]:
        state = {
            "conversation_id": str(uuid.uuid4()),
            "selected_kbs": ["moe"],
            "locale": "ar",
            "capability_outputs": [{"capability": "docs", "sources": [source]}],
        }
        assert await load_evidence(state, ctx) == [] and ctx.evidence == {}


async def test_a_conversation_persisted_before_collections_reopens_with_its_citations(
    client, services, litellm
):
    """G9: messages and citations in the phase 04 shape (ADR-0193) come back unchanged."""
    user = f"u-g9-{uuid.uuid4().hex[:8]}"
    conversation = str(uuid.uuid4())
    principal = {"user_id": user, "tenant_id": "default", "permissions": ["cap:general"]}
    await post_turn(client, conversation_id=conversation, principal=principal, message="سؤال أول")
    old_citation = {
        "id": "c-old",
        "title": "لائحة الإجازات.pdf",
        "source": "doc",
        "url": None,
        "kb": None,
        "locator": None,
        "document_id": "d-old",
        "pages": [2, 3],
    }
    await services.graph.aupdate_state(
        {"configurable": {"thread_id": conversation}},
        {
            "messages": [
                {"role": "user", "content": "كم مدة إجازة الأمومة؟", "attachment_ids": ["d-old"]},
                {
                    "role": "assistant",
                    "content": "عشرة أسابيع.",
                    "turn_id": "t" * 32,
                    "citations": [old_citation],
                },
            ]
        },
        as_node="persist_state",
    )
    response = await client.get(
        f"/v1/conversations/{conversation}/messages",
        headers={"X-User-Id": user, "X-Tenant-Id": "default", "X-Internal-Token": "test-internal-token"},
    )
    assert response.status_code == 200, response.text
    *_, question, reply = response.json()["messages"]
    assert (question["role"], question["text"]) == ("user", "كم مدة إجازة الأمومة؟")
    assert (reply["text"], reply["turn_id"], reply["citations"]) == ("عشرة أسابيع.", "t" * 32, [old_citation])


async def test_a_docs_turn_persists_citations_without_backend_or_collection(services, litellm):
    """G9: what this phase adds to a source (backend, collection) stays internal."""
    from app.api.events import Citation

    harness = Harness("fake", services)
    principal = Principal(user_id=f"u-g9d-{uuid.uuid4().hex[:8]}", permissions=("cap:general", "cap:docs"))
    conversation = str(uuid.uuid4())
    scope = DocumentScope.session(
        tenant_id=principal.tenant_id, user_id=principal.user_id, conversation_id=conversation
    )
    leave = ["المادة الثالثة: تمنح الموظفة إجازة أمومة مدتها عشرة أسابيع براتب كامل."]
    document = await harness.upload(scope, "لائحة.pdf", leave)
    await harness.ingest(document)
    request = TurnRequest(
        conversation_id=conversation,
        principal=principal,
        message="كم مدة إجازة الأمومة براتب كامل؟",
        attachment_ids=[document],
        locale="ar",
    )
    events = [e async for e in TurnRunner(harness.services).stream(request, "req-g9")]
    assert events[-1].type == "done"
    state = await harness.services.graph.aget_state({"configurable": {"thread_id": conversation}})
    [reply] = [m for m in state.values["messages"] if getattr(m, "type", "") == "ai"]
    allowed = set(Citation.model_fields)
    for citation in reply.additional_kwargs.get("citations") or []:
        assert set(citation) <= allowed and not {"backend", "collection"} & set(citation)
    for source in state.values["capability_outputs"][0]["sources"] or []:
        assert source["backend"] == "fake" and source["collection"] == f"sess_{conversation}"
