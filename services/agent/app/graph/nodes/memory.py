"""write_memory (README §8, ADR-0058): runs after persist_state. The `extractor` role
reads this turn's exchange as persisted — the post-guard `content` of the user
message and the reply, never `shown_answer` (ADR-0048) — and the kept candidates
are stored for the turn's (tenant, user). Nothing is written when that user's
memory is off or a guard flagged the turn.

Synchronous in phase 02 (asynchronous writing is phase 09); its latency goes on
the turn span as `memory.write_ms`. The reply is already persisted, so a failure
here is recorded on the span and never fails the turn."""

import logging
import time

from langgraph.runtime import Runtime
from opentelemetry import trace

from app.context.package import AssemblyRequest
from app.graph.calls import call_model, prepare_call
from app.graph.deps import TurnContext
from app.graph.nodes import traced
from app.graph.state import AgentState
from app.memory.policy import guard_flagged, parse_candidates
from app.memory.types import MemoryScope

log = logging.getLogger(__name__)


def _exchange(state: AgentState) -> list[dict] | None:
    """The two messages persist_state appended for this turn, reduced to role and
    content, the post-guard text every later model call reads."""
    messages = state.get("messages") or []
    if len(messages) < 2:
        return None
    question, reply = messages[-2], messages[-1]
    if getattr(question, "type", None) != "human" or getattr(reply, "type", None) != "ai":
        return None
    if (reply.additional_kwargs or {}).get("turn_id") != state.get("turn_id"):
        return None
    return [{"role": "user", "content": question.content}, {"role": "assistant", "content": reply.content}]


async def _write(state: AgentState, ctx: TurnContext) -> tuple[str, int, int]:
    """(outcome, tokens_in, tokens_out)"""
    memory = ctx.services.memory
    scope = MemoryScope.of(ctx.principal)
    if not await memory.store.enabled(scope):
        return "disabled", 0, 0
    if guard_flagged(state.get("guard_events") or []):
        return "guard_flagged", 0, 0
    exchange = _exchange(state)
    if exchange is None:
        return "no_exchange", 0, 0

    node = ctx.services.assembler.node("memory_writer")
    request = AssemblyRequest(
        node="memory_writer",
        needs=node.needs,
        system_prompt=node.system_prompt,
        user_prompt=node.request_prompt,
        locale=state["locale"],
        state={"messages": exchange},
        window_messages=len(exchange),
    )
    result = await call_model(ctx, await prepare_call(ctx, request, role="extractor"))
    candidates = parse_candidates(
        result.text,
        memory.config,
        source=exchange[0]["content"],
        scripts=ctx.services.validation.scripts_for(state["locale"]),
    )
    stored = await memory.writer.store_candidates(scope, candidates, source_turn=ctx.turn_id)
    span = trace.get_current_span()
    span.set_attribute("memory.candidates", len(candidates))
    span.set_attribute("memory.stored", stored)
    return ("stored" if stored else "nothing_stored"), result.tokens_in, result.tokens_out


@traced("write_memory")
async def write_memory(state: AgentState, runtime: Runtime[TurnContext]) -> dict:
    ctx = runtime.context
    span = trace.get_current_span()
    started = time.monotonic()
    tokens_in = tokens_out = 0
    try:
        outcome, tokens_in, tokens_out = await _write(state, ctx)
    except Exception as exc:
        outcome = "failed"
        span.add_event("memory.write_failed", {"error": type(exc).__name__})
        log.warning("memory write failed for turn %s: %s", ctx.turn_id, type(exc).__name__)
    finally:
        ctx.timings["memory.write_ms"] = int((time.monotonic() - started) * 1000)
    span.set_attribute("memory.write.outcome", outcome)
    if not (tokens_in or tokens_out):
        return {}
    return {
        "tokens_in": (state.get("tokens_in") or 0) + tokens_in,
        "tokens_out": (state.get("tokens_out") or 0) + tokens_out,
    }
