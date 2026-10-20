"""Tool scope and declared tools (S1, ADR-0160). The broker builds a tool's scope from the
verified principal and the turn's conversation; `docs_search` takes no owner, so a call that
names another owner fails validation, and a conversation id alone reaches nothing of another
user's. A capability may call only the tools its manifest declares: anything else is refused
before it runs and audited."""

import uuid

import pytest

from app.api.runner import TurnRunner
from app.api.schemas import TurnRequest
from app.capabilities.docs.tools import SearchArgs
from app.identity import Principal
from app.rag.backend import DocumentScope
from tests.fixtures.backends import Harness

pytestmark = pytest.mark.integration

LEAVE = ["المادة الثالثة: تمنح إجازة الأمومة لمدة عشرة أسابيع براتب كامل."]


@pytest.mark.parametrize("field", ["tenant_id", "user_id", "collection", "attachment_ids", "scope"])
def test_docs_search_has_no_field_that_names_an_owner_or_a_collection(field):
    with pytest.raises(ValueError):
        SearchArgs.model_validate({"query": "إجازة الأمومة", field: "other"})


async def test_a_call_naming_another_owner_never_runs_and_a_conversation_id_alone_reaches_nothing(
    services, litellm
):
    harness = Harness("internal", services)
    victim = Principal(user_id=f"u-victim-{uuid.uuid4().hex[:6]}", permissions=("cap:docs",))
    conversation = str(uuid.uuid4())
    scope = DocumentScope.session(
        tenant_id=victim.tenant_id, user_id=victim.user_id, conversation_id=conversation
    )
    document = await harness.upload(scope, "لائحة.pdf", LEAVE)
    assert await harness.ingest(document) == "indexed"

    intruder = Principal(user_id=f"u-intruder-{uuid.uuid4().hex[:6]}", permissions=("cap:docs",))
    named = await services.broker.execute(
        "docs_search",
        {"query": "إجازة الأمومة", "user_id": victim.user_id, "tenant_id": victim.tenant_id},
        caller="docs",
        principal=intruder,
        conversation_id=conversation,
    )
    assert (named.status, named.error) == ("error", "invalid_arguments")

    # The victim's conversation id with the intruder's own principal: the scope is the intruder's.
    guessed = await services.broker.execute(
        "docs_search",
        {"query": "إجازة الأمومة"},
        caller="docs",
        principal=intruder,
        conversation_id=conversation,
    )
    assert (guessed.status, guessed.content, guessed.sources) == ("ok", "", [])

    owned = await services.broker.execute(
        "docs_search",
        {"query": "إجازة الأمومة"},
        caller="docs",
        principal=victim,
        conversation_id=conversation,
    )
    assert owned.status == "ok" and [s["attachment_id"] for s in owned.sources] == [document]


async def test_a_capability_calling_a_tool_it_did_not_declare_is_refused_and_audited(probe_services, litellm):
    principal = Principal(
        user_id=f"u-rogue-{uuid.uuid4().hex[:6]}", permissions=("cap:general", "cap:docs", "cap:rogue")
    )
    request = TurnRequest(
        conversation_id=str(uuid.uuid4()), principal=principal, message="مارقة: ابحث في الملفات", locale="ar"
    )
    events = [event async for event in TurnRunner(probe_services).stream(request, "req-rogue")]
    done = events[-1]
    assert done.type == "done" and done.data.state_digest.capabilities == ["rogue"]
    assert not [e for e in events if e.type in ("tool_start", "tool_end")]  # nothing ran
    [audited] = [g for g in done.data.guard_audit if g.reason == "undeclared_tool"]
    assert audited.checkpoint == "PRE_TOOL" and audited.action == "block"
    assert "rogue" in audited.detail and "docs_search" in audited.detail
    # Audit only: the client's guard events never carry it.
    assert not [g for g in done.data.guard_events if g.checkpoint == "PRE_TOOL"]
