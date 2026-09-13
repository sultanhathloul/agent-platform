"""general (UC5): no tools and no evidence. It runs on the same contract as
every capability (RuntimeContext, ADR-0038) and contributes an empty finding,
so the core answer node answers from the model's own knowledge on this
capability's tier floor (manifest `model.min_tier`, ADR-0014)."""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from app.capabilities.contract import CapabilityInput, CapabilityOutput, RuntimeContext


class _State(TypedDict, total=False):
    input: CapabilityInput
    output: CapabilityOutput


def _run(state: _State, runtime: Runtime[RuntimeContext]) -> dict:
    return {"output": CapabilityOutput(findings="", cap_state=dict(runtime.context.cap_state))}


def build() -> StateGraph:
    # Returns the uncompiled StateGraph; the core compiles it with no
    # checkpointer, store or interrupts (ADR-0053).
    graph = StateGraph(_State, context_schema=RuntimeContext)
    graph.add_node("run", _run)
    graph.add_edge(START, "run")
    graph.add_edge("run", END)
    return graph
