import functools
from collections.abc import Awaitable, Callable

from langgraph.runtime import Runtime

from app.graph.deps import TurnContext
from app.graph.state import AgentState
from app.observability.otel import tracer

Node = Callable[[AgentState, Runtime[TurnContext]], Awaitable[dict]]


def traced(name: str) -> Callable[[Node], Node]:
    """One `node.<name>` span per graph node (README §12)."""

    def wrap(fn: Node) -> Node:
        @functools.wraps(fn)
        async def node(state: AgentState, runtime: Runtime[TurnContext]) -> dict:
            with tracer.start_as_current_span(f"node.{name}"):
                return await fn(state, runtime)

        return node

    return wrap


def emit(runtime: Runtime[TurnContext], type_: str, data: dict) -> None:
    """Streams one event to the client (wrapped into the envelope by the runner)."""
    runtime.stream_writer({"type": type_, "data": data})


def retract_streamed(state: AgentState, runtime: Runtime[TurnContext], reason: str) -> None:
    """Withdraws the answer tokens already streamed this turn before the turn ends
    in an error. Interim until phase 03 buffers output before it leaves (ADR-0039)."""
    if state.get("shown_answer"):
        emit(runtime, "retract", {"reason": reason})
