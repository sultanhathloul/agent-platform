"""The manifest's `context.needs` drives the answer package (README §18.4, owner decision C2);
config/context.yaml supplies the defaults only. A capability declaring `needs: []` gets the
system instructions and the user message and nothing else; a `general` turn in a conversation
with indexed files carries no evidence block; two capabilities get their needs together."""

import dataclasses
import uuid

import pytest
from tests.fixtures.backends import Harness
from tests.fixtures.documents import LEAVE_REGULATION
from tests.fixtures.replace import replacing

from app.api.runner import TurnRunner
from app.api.schemas import TurnRequest
from app.capabilities.contract import ContextSpec
from app.identity import Principal
from app.rag.backend import DocumentScope

pytestmark = pytest.mark.integration

EVIDENCE = {"findings", "retrieval", "tool_results"}


class _Packages:
    """Records the block names of every answer package."""

    def __init__(self, inner):
        self.inner = inner
        self.answers: list[list[str]] = []

    async def call(self, decision, package, **kwargs):
        if package.node == "answer":
            self.answers.append([b.name for b in package.blocks])
        return await self.inner.call(decision, package, **kwargs)


async def _turns(services, principal, conversation, messages, ids=()):
    recorder = _Packages(services.gateway)
    recorded = dataclasses.replace(services, gateway=recorder)
    for index, message in enumerate(messages):
        request = TurnRequest(
            conversation_id=conversation,
            principal=principal,
            message=message,
            attachment_ids=list(ids) if index == 0 else [],
            locale="ar",
        )
        events = [e async for e in TurnRunner(recorded).stream(request, f"req-needs-{index}")]
        assert events[-1].type == "done", events[-1]
    return recorder.answers


def test_none_takes_the_defaults_and_an_empty_list_takes_nothing():
    assert ContextSpec().needs_or(["window", "memory"]) == ["window", "memory"]
    assert ContextSpec(needs=[]).needs_or(["window", "memory"]) == []


async def test_needs_empty_yields_no_optional_blocks(probe_services, litellm):
    """`echo` declares `needs: []`: with a history, a summary-worthy conversation and memory
    switched on, its answer still sees only the system instructions and the question."""
    principal = Principal(user_id=f"u-needs-{uuid.uuid4().hex[:6]}", permissions=("cap:general", "cap:echo"))
    conversation = str(uuid.uuid4())
    answers = await _turns(
        probe_services,
        principal,
        conversation,
        ["ما عاصمة فرنسا؟", "وما عاصمة إسبانيا؟", "صدى: وما عاصمة إيطاليا؟"],
    )
    assert "window" in answers[1]  # general's answer has the history it declares
    assert answers[2] == ["system", "user_message"]


async def test_a_general_turn_in_a_conversation_with_indexed_files_carries_no_evidence(services, litellm):
    harness = Harness("internal", services)
    principal = Principal(user_id=f"u-needs-{uuid.uuid4().hex[:6]}", permissions=("cap:general", "cap:docs"))
    conversation = str(uuid.uuid4())
    scope = DocumentScope(
        tenant_id=principal.tenant_id, user_id=principal.user_id, conversation_id=conversation
    )
    document = await harness.upload(scope, "لائحة.pdf", ["\n".join(page) for page in LEAVE_REGULATION])
    assert await harness.ingest(document) == "indexed"
    routed, _ = replacing(services, {"router": '{"capabilities": ["general"]}'})
    answers = await _turns(
        routed,
        principal,
        conversation,
        ["كم مدة إجازة الأمومة؟", f"ما عاصمة فرنسا؟ {uuid.uuid4().hex[:6]}"],
        ids=[document],
    )
    assert "findings" in answers[0]  # the docs turn
    assert not EVIDENCE & set(answers[1])  # the general turn
    assert "window" in answers[1]


async def test_two_capabilities_get_their_needs_together(probe_services, litellm):
    """probe declares [system, user_message], echo []: together, still nothing else."""
    principal = Principal(
        user_id=f"u-needs-{uuid.uuid4().hex[:6]}", permissions=("cap:general", "cap:echo", "cap:probe")
    )
    answers = await _turns(probe_services, principal, str(uuid.uuid4()), ["صدى مسبار"])
    assert answers == [["system", "user_message"]]
