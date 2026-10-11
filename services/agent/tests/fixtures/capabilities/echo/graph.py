"""Test-only capability (ADR-0121): no tools and an empty finding, like general, but
routed by keyword so it can be selected beside probe."""

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
    # Returns the uncompiled StateGraph; the core compiles it (ADR-0053).
    graph = StateGraph(_State, context_schema=RuntimeContext)
    graph.add_node("run", _run)
    graph.add_edge(START, "run")
    graph.add_edge("run", END)
    return graph
