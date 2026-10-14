"""Phase 01 tech debt closed in phase 04 (F4), now that two real capabilities exist:
- at runtime, two capabilities in one turn each see and write only their own `cap_state`
  namespace (README §18.4; ADR-0047, ADR-0062 is the lint half);
- a capability that runs past its manifest's `limits.timeout_s` ends the turn as a
  retryable `capability_failed` and persists nothing (ADR-0053)."""

import asyncio
import dataclasses
import uuid
from typing import Any, TypedDict

import pytest
from langgraph.graph import END, START, StateGraph

from app.api.runner import TurnRunner
from app.api.schemas import TurnRequest
from app.capabilities.contract import CapabilityInput, CapabilityOutput, Limits, RuntimeContext
from app.capabilities.registry import CapabilityRegistry, LoadedCapability
from app.identity import Principal

pytestmark = pytest.mark.integration

BOTH = "صدى مسبار"  # matches echo's and probe's keywords


class _State(TypedDict, total=False):
    input: CapabilityInput
    output: Any


def _recording(slug: str, seen: list) -> StateGraph:
    async def run(state, runtime):
        seen.append((slug, dict(state["input"].cap_state), dict(runtime.context.cap_state)))
        updated = {
            **runtime.context.cap_state,
            "owner": slug,
            "turns": runtime.context.cap_state.get("turns", 0) + 1,
        }
        return {"output": CapabilityOutput(findings="", cap_state=updated)}

    graph = StateGraph(_State, context_schema=RuntimeContext)
    graph.add_node("run", run)
    graph.add_edge(START, "run")
    graph.add_edge("run", END)
    return graph


def _services_with(probe_services, graphs: dict[str, StateGraph], **manifest_updates):
    loaded = []
    for slug, graph in graphs.items():
        base = probe_services.registry.get(slug)
        manifest = base.manifest.model_copy(update=manifest_updates.get(slug, {}))
        loaded.append(LoadedCapability(manifest=manifest, graph=graph.compile(), path=base.path))
    return dataclasses.replace(probe_services, registry=CapabilityRegistry(loaded))


async def _turn(services, principal, conversation, message=BOTH):
    request = TurnRequest(conversation_id=conversation, principal=principal, message=message, locale="ar")
    return [event async for event in TurnRunner(services).stream(request, "req-f4")]


async def test_two_capabilities_in_one_turn_each_see_and_write_only_their_own_namespace(
    probe_services, litellm
):
    seen: list = []
    services = _services_with(
        probe_services, {"echo": _recording("echo", seen), "probe": _recording("probe", seen)}
    )
    principal = Principal(
        user_id=f"u-f4-{uuid.uuid4().hex[:6]}", permissions=("cap:general", "cap:probe", "cap:echo")
    )
    conversation = str(uuid.uuid4())

    first = await _turn(services, principal, conversation)
    assert first[-1].type == "done" and sorted(first[-1].data.state_digest.capabilities) == ["echo", "probe"]
    assert sorted(seen) == [("echo", {}, {}), ("probe", {}, {})]

    seen.clear()
    second = await _turn(services, principal, conversation)
    assert second[-1].type == "done"
    for slug, from_input, from_runtime in seen:
        # Each capability got its own slice, never the other's or the whole cap_state.
        assert from_input == from_runtime == {"owner": slug, "turns": 1}

    state = (await services.graph.aget_state({"configurable": {"thread_id": conversation}})).values
    assert state["cap_state"] == {
        "cap.echo": {"owner": "echo", "turns": 2},
        "cap.probe": {"owner": "probe", "turns": 2},
    }


async def test_a_capability_past_its_timeout_fails_the_turn_retryably_and_persists_nothing(
    probe_services, litellm
):
    async def slow(state, runtime):
        await asyncio.sleep(5)
        return {"output": CapabilityOutput(findings="late", cap_state={})}

    graph = StateGraph(_State, context_schema=RuntimeContext)
    graph.add_node("run", slow)
    graph.add_edge(START, "run")
    graph.add_edge("run", END)
    services = _services_with(probe_services, {"echo": graph}, echo={"limits": Limits(timeout_s=0.3)})
    principal = Principal(user_id=f"u-f4t-{uuid.uuid4().hex[:6]}", permissions=("cap:general", "cap:echo"))
    conversation = str(uuid.uuid4())

    started = asyncio.get_running_loop().time()
    events = await _turn(services, principal, conversation, "صدى")
    elapsed = asyncio.get_running_loop().time() - started

    error = events[-1]
    assert error.type == "error" and error.data.code == "capability_failed" and error.data.retryable
    assert elapsed < 4, f"the turn waited {elapsed:.1f}s for a capability limited to 0.3s"
    state = (await services.graph.aget_state({"configurable": {"thread_id": conversation}})).values
    assert not state.get("messages")


async def test_docs_and_general_in_one_turn_and_a_cross_namespace_write_stays_in_its_own_slice(
    services, litellm
):
    """The real `docs` and `general` run together (the router chooses both). `docs` is wrapped so
    that, after its real run, it tries to write `general`'s namespace twice: by returning a
    `cap.general` key, and by writing into its RuntimeContext's copy. Neither reaches `general`."""
    from tests.fixtures.backends import Harness
    from tests.fixtures.documents import LEAVE_REGULATION
    from tests.fixtures.replace import replacing

    from app.rag.backend import DocumentScope

    real_docs = services.registry.get("docs")

    async def hijacking_docs(state, runtime):
        result = await real_docs.graph.ainvoke({"input": state["input"]}, context=runtime.context)
        output = result["output"]
        runtime.context.cap_state["cap.general"] = {"hijacked": "through the runtime copy"}
        cap_state = {**output.cap_state, "cap.general": {"hijacked": "through the output"}}
        return {"output": output.model_copy(update={"cap_state": cap_state})}

    graph = StateGraph(_State, context_schema=RuntimeContext)
    graph.add_node("run", hijacking_docs)
    graph.add_edge(START, "run")
    graph.add_edge("run", END)
    general = services.registry.get("general")
    wrapped = dataclasses.replace(
        services,
        registry=CapabilityRegistry(
            [
                LoadedCapability(
                    manifest=real_docs.manifest,
                    graph=graph.compile(),
                    path=real_docs.path,
                    tools=real_docs.tools,
                ),
                general,
            ]
        ),
    )
    principal = Principal(user_id=f"u-iso-{uuid.uuid4().hex[:6]}", permissions=("cap:general", "cap:docs"))
    conversation = str(uuid.uuid4())
    harness = Harness("internal", wrapped)
    scope = DocumentScope(
        tenant_id=principal.tenant_id, user_id=principal.user_id, conversation_id=conversation
    )
    document = await harness.upload(scope, "لائحة.pdf", ["\n".join(page) for page in LEAVE_REGULATION])
    assert await harness.ingest(document) == "indexed"
    first = await _turn(wrapped, principal, conversation, "كم مدة إجازة الأمومة؟")
    assert first[-1].type == "done"

    routed, _ = replacing(wrapped, {"router": '{"capabilities": ["docs", "general"]}'})
    second = await _turn(routed, principal, conversation, f"وماذا عن الإجازة السنوية؟ {uuid.uuid4().hex[:6]}")
    assert second[-1].type == "done" and second[-1].data.state_digest.capabilities == ["docs", "general"]
    state = (await routed.graph.aget_state({"configurable": {"thread_id": conversation}})).values
    assert state["cap_state"]["cap.general"] == {}  # general's own slice, untouched
    docs = state["cap_state"]["cap.docs"]
    assert docs["searches"] == 2  # docs' real work, both turns
    assert docs["cap.general"] == {
        "hijacked": "through the output"
    }  # a key inside docs' own slice, nothing more
    assert set(state["cap_state"]) <= {"cap.docs", "cap.general"}
