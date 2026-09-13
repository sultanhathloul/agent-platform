"""Test-only capability (ADR-0038): one broker call, one model call on a step
override, and a state update, all through the RuntimeContext."""

import asyncio
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from app.capabilities.contract import CapabilityInput, CapabilityOutput, RuntimeContext, Source

# A test sets this to prove events stream while the capability runs: the
# capability waits here until the test has received its tool_end event.
GATE: asyncio.Event | None = None


class _State(TypedDict, total=False):
    input: CapabilityInput
    output: CapabilityOutput


async def _run(state: _State, runtime: Runtime[RuntimeContext]) -> dict:
    rt = runtime.context
    looked_up = await rt.broker.execute("probe_lookup", {"text": state["input"].message})
    if GATE is not None:
        await asyncio.wait_for(GATE.wait(), timeout=5)
    draft = await rt.models.call("reasoning", step="draft")
    return {
        "output": CapabilityOutput(
            findings=f"{looked_up.content}\n{draft.text}",
            sources=[Source(id="probe-1", title="probe", kind="doc")],
            cap_state={"runs": rt.cap_state.get("runs", 0) + 1},
        )
    }


def build():
    graph = StateGraph(_State, context_schema=RuntimeContext)
    graph.add_node("run", _run)
    graph.add_edge(START, "run")
    graph.add_edge("run", END)
    return graph.compile()
