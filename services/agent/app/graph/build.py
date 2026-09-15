"""The main graph (README §5.2). Capability-agnostic: capabilities run inside
`run_capabilities`, never as branches here. Reflection, attachment ingestion
and the HITL interrupt join in later phases."""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from app.graph.deps import TurnContext
from app.graph.nodes.answer import answer
from app.graph.nodes.assemble import assemble_context
from app.graph.nodes.capabilities import run_capabilities
from app.graph.nodes.guard import guard_data, guard_input, guard_output, guard_tool_list
from app.graph.nodes.identity import load_identity_and_state
from app.graph.nodes.persist import persist_state
from app.graph.nodes.route import route
from app.graph.nodes.validate import validate
from app.graph.state import AgentState

PIPELINE = [
    ("load_identity_and_state", load_identity_and_state),
    ("guard_input", guard_input),  # checkpoint 1
    ("assemble_context", assemble_context),
    ("route", route),
    ("guard_tool_list", guard_tool_list),  # checkpoint 2
    ("run_capabilities", run_capabilities),  # checkpoints 3, 4 inside the broker
    ("answer", answer),  # checkpoint 7 inside the tier resolver
    ("validate", validate),
    ("guard_output", guard_output),  # checkpoint 5
    ("guard_data", guard_data),  # checkpoint 6
    ("persist_state", persist_state),
]


def build_graph(checkpointer: BaseCheckpointSaver):
    """Compiled on the Postgres checkpointer the service opens at startup (ADR-0055)."""
    graph = StateGraph(AgentState, context_schema=TurnContext)
    previous = START
    for name, node in PIPELINE:
        graph.add_node(name, node)
        graph.add_edge(previous, name)
        previous = name
    graph.add_edge(previous, END)
    return graph.compile(checkpointer=checkpointer)
