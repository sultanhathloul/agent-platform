"""Test-only capability (ADR-0160): one call to a tool its manifest does not declare. The
finding reports what the broker answered, so a test can see the refusal from both sides."""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from app.capabilities.contract import CapabilityInput, CapabilityOutput, RuntimeContext


class _State(TypedDict, total=False):
    input: CapabilityInput
    output: CapabilityOutput


async def _run(state: _State, runtime: Runtime[RuntimeContext]) -> dict:
    rt = runtime.context
    result = await rt.broker.execute("docs_search", {"query": state["input"].message})
    return {"output": CapabilityOutput(findings="", cap_state={"refused": f"{result.status}:{result.error}"})}


def build() -> StateGraph:
    # Returns the uncompiled StateGraph; the core compiles it (ADR-0053).
    graph = StateGraph(_State, context_schema=RuntimeContext)
    graph.add_node("run", _run)
    graph.add_edge(START, "run")
    graph.add_edge("run", END)
    return graph
