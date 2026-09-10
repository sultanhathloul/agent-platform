"""general (UC5): no tools and no evidence. The capability contributes an
empty finding, so the core answer node answers from the model's own knowledge
on this capability's tier floor (fast)."""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.capabilities.contract import CapabilityInput, CapabilityOutput


class _State(TypedDict, total=False):
    input: CapabilityInput
    output: CapabilityOutput


def _run(state: _State) -> dict:
    return {"output": CapabilityOutput(findings="", cap_state=state["input"].cap_state)}


def build():
    graph = StateGraph(_State)
    graph.add_node("run", _run)
    graph.add_edge(START, "run")
    graph.add_edge("run", END)
    return graph.compile()
