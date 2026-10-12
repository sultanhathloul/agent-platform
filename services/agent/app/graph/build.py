"""The main graph (README §5.2). Capability-agnostic: capabilities run inside
`run_capabilities`, never as branches here. Reflection and the HITL interrupt
join in later phases; `ingest_attachments` (phase 04) only reads attachment status,
ingestion runs in the background worker. `release_answer` (phase 03) streams
an answer that was buffered for its OUTPUT and DATA checks (D3)."""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from app.graph.deps import TurnContext
from app.graph.nodes.answer import answer
from app.graph.nodes.assemble import assemble_context, fold_summary
from app.graph.nodes.attachments import ingest_attachments
from app.graph.nodes.attribute import attribute_citations
from app.graph.nodes.capabilities import run_capabilities
from app.graph.nodes.guard import guard_data, guard_input, guard_output, guard_tool_list, release_answer
from app.graph.nodes.identity import load_identity_and_state
from app.graph.nodes.memory import write_memory
from app.graph.nodes.persist import persist_state
from app.graph.nodes.route import route
from app.graph.nodes.validate import validate
from app.graph.state import AgentState

PIPELINE = [
    ("load_identity_and_state", load_identity_and_state),
    ("guard_input", guard_input),  # checkpoint 1
    ("ingest_attachments", ingest_attachments),  # reads the conversation's files and their status (A1)
    ("assemble_context", assemble_context),
    ("route", route),
    ("guard_tool_list", guard_tool_list),  # checkpoint 2
    ("run_capabilities", run_capabilities),  # checkpoints 3, 4 inside the broker
    ("answer", answer),  # checkpoint 7 inside the tier resolver; checkpoint 6 per sentence while streaming
    ("attribute_citations", attribute_citations),  # capability-agnostic page attribution (C1)
    ("validate", validate),
    ("guard_output", guard_output),  # checkpoint 5
    ("guard_data", guard_data),  # checkpoint 6 on the whole answer in buffered mode (D3)
    ("release_answer", release_answer),  # buffered mode: the checked answer streams now
    ("persist_state", persist_state),
    ("fold_summary", fold_summary),  # rolling summary after the reply, never before the first token
    ("write_memory", write_memory),  # synchronous in phase 02 (ADR-0058)
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
