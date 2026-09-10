from langgraph.runtime import Runtime
from opentelemetry import trace

from app.context.selectors.blocks import _message_role_and_text
from app.graph.deps import TurnContext
from app.graph.nodes import traced
from app.graph.state import AgentState


@traced("assemble_context")
async def assemble_context(state: AgentState, runtime: Runtime[TurnContext]) -> dict:
    """Turn-level context preparation (README §5.2). The per-call assembly
    happens right before each model call (README §7). Phase 01 measures history
    size on the span; phase 02 triggers the rolling summary here once history
    passes 8k tokens."""
    counter = runtime.context.services.assembler.steps[0].deps.counter
    texts = [parsed[1] for m in state.get("messages") or [] if (parsed := _message_role_and_text(m))]
    span = trace.get_current_span()
    span.set_attribute("context.history_messages", len(texts))
    span.set_attribute("context.history_tokens", sum(counter.count(t) for t in texts))
    return {}
