"""answer (README §5.2): the reasoning model synthesizes the capability
outputs into the reply. The tier floor comes from the selected capabilities'
manifests (§19.3).

Two modes (D3, ADR-0039 full fix):
- streaming (the default): tokens go through the DATA sentence buffer
  (app/graph/streaming.py), so each sentence is released only after DATA ran on
  it, and redaction happens before the text leaves the agent;
- buffered: when an enabled OUTPUT policy that can block, rewrite or redact applies
  to the principal's scope, nothing streams here. OUTPUT and DATA run on the whole
  answer, and `release_answer` streams the result.

An answer that fails a script check named in config/validation.yaml is retried
once, at the configured temperature, on the same context package; in streaming
mode `retract` withdraws the released text and the retry streams in its place.
`validate` ends the turn only if the retry fails too (ADR-0061)."""

import math
from collections.abc import Sequence

from langgraph.runtime import Runtime
from opentelemetry import trace

from app.context.package import BLOCK_NAMES, SKILL_BLOCK, AssemblyRequest, BlockName, SkillBlockRequest
from app.errors import TurnError
from app.graph.calls import call_model, prepare_call, tier_event
from app.graph.deps import TurnContext
from app.graph.evidence import load_evidence
from app.graph.guarding import guard_context
from app.graph.nodes import emit, traced
from app.graph.state import AgentState
from app.graph.streaming import DataStream
from app.memory.types import MemoryScope
from app.platform_settings import effective
from app.resilience import deadline
from app.text.script import script_issues

RETRY_REASON = "answer_retry"


def answer_needs(state: AgentState, ctx: TurnContext) -> list[BlockName]:
    """The blocks of the answer package: what the turn's capabilities declare in
    `context.needs`, together, in pipeline order (owner decision C2). A capability that
    declares nothing takes the answer node's defaults from config/context.yaml; the system
    instructions and the user message are always there."""
    default = ctx.services.assembler.node("answer").needs
    wanted: set[str] = set()
    for slug in state["intent"]["capabilities"]:
        wanted.update(ctx.services.registry.get(slug).manifest.context.needs_or(default))
    if state.get("skill"):
        wanted.add(SKILL_BLOCK)  # the turn's skill: requested by the answer node only (ADR-0300)
    return [block for block in BLOCK_NAMES if block in wanted]


def skill_request(state: AgentState, ctx: TurnContext) -> SkillBlockRequest | None:
    """The turn's selected skill as the `skill` block's request, or None."""
    selected = state.get("skill")
    skill = ctx.services.skills.get(selected["slug"]) if selected else None
    if skill is None:
        return None
    return SkillBlockRequest(
        slug=skill.slug, version=skill.manifest.version, prompt=skill.manifest.instructions_prompt
    )


def answer_request(
    state: AgentState,
    ctx: TurnContext,
    *,
    policy_hints: Sequence[str] = (),
    retrieved: Sequence[dict] = (),
) -> AssemblyRequest:
    node = ctx.services.assembler.node("answer")
    return AssemblyRequest(
        node="answer",
        needs=answer_needs(state, ctx),
        system_prompt=node.system_prompt,
        locale=state["locale"],
        state=dict(state),
        # The answer node synthesizes; it selects no tools, so none are listed.
        findings=[
            {"capability": o["capability"], "findings": o["findings"]} for o in state["capability_outputs"]
        ],
        retrieved=list(retrieved),
        policy_hints=list(policy_hints),
        skill=skill_request(state, ctx),
        memory_scope=MemoryScope.of(ctx.principal),
    )


async def prepare_answer(state: AgentState, ctx: TurnContext, *, policy_hints: Sequence[str] = ()):
    # Chunk text is fetched now, never earlier, and only for a turn whose capabilities need
    # the retrieval block (S4, C2).
    retrieved = await load_evidence(state, ctx) if "retrieval" in answer_needs(state, ctx) else []
    selected = [ctx.services.registry.get(slug) for slug in state["intent"]["capabilities"]]
    max_inputs = [
        c.manifest.context.max_input_tokens for c in selected if c.manifest.context.max_input_tokens
    ]
    return await prepare_call(
        ctx,
        answer_request(state, ctx, policy_hints=policy_hints, retrieved=retrieved),
        role="reasoning",
        step="answer",
        capabilities=selected,
        max_input=max(max_inputs) if max_inputs else None,
    )


@traced("answer")
async def answer(state: AgentState, runtime: Runtime[TurnContext]) -> dict:
    ctx = runtime.context
    services = ctx.services
    locale = state["locale"]
    buffered = services.guard.requires_buffering(ctx.principal, guard_context(state, ctx))
    span = trace.get_current_span()
    span.set_attribute("answer.mode", "buffered" if buffered else "streaming")
    skill = ctx.services.skills.get(state["skill"]["slug"]) if state.get("skill") else None
    if skill is not None:
        # Before any token: the client names the skill the answer is written with (ADR-0301).
        span.set_attribute("answer.skill", skill.manifest.ref)
        emit(runtime, "skill", {"slug": skill.slug, "label": skill.manifest.name.model_dump()})

    prepared = await prepare_answer(state, ctx)
    emit(runtime, "tier", tier_event(prepared.decision))

    async def attempt(temperature: float | None = None) -> tuple[str, str, int, int]:
        """(model text, text released to the client, tokens in, tokens out)"""
        if buffered:
            result = await call_model(ctx, prepared, temperature=temperature)
            return result.text, "", result.tokens_in, result.tokens_out
        stream = DataStream(ctx, state)
        try:
            result = await call_model(ctx, prepared, on_token=stream.feed, temperature=temperature)
        except BaseException:
            await stream.abort()
            raise
        try:
            released = await stream.close()
        except TurnError as exc:
            # A DATA block or an unavailable policy mid-answer: withdraw what already left.
            if stream.released:
                emit(runtime, "retract", {"reason": exc.code})
            raise
        return result.text, released, result.tokens_in, result.tokens_out

    # The answer, its retry included, ends post_answer_reserve_s before the turn's deadline, so the
    # steps after it always have that room (ADR-0263).
    with deadline.within(math.inf, margin_s=effective.turn(services.turn).post_answer_reserve_s):
        text, released, tokens_in, tokens_out = await attempt()

        retry = services.validation.retry
        scripts = services.validation.scripts_for(locale)
        issues = script_issues(text, scripts) if scripts else []
        retrying = bool(retry.attempts and set(issues) & set(retry.issues))
        span.set_attribute("answer.retry", retrying)
        if retrying:
            span.set_attribute("answer.retry.issues", ",".join(issues))
            if released:
                emit(runtime, "retract", {"reason": RETRY_REASON})
            text, released, retry_in, retry_out = await attempt(retry.temperature)
            tokens_in += retry_in
            tokens_out += retry_out
            span.set_attribute("answer.retry.passed", not script_issues(text, scripts))
    if not buffered:
        ctx.answer_sent = True  # every token has left (streaming); buffered: release_answer

    return {
        "answer_mode": "buffered" if buffered else "streaming",
        # Streaming: the released, post-DATA text is the answer and what the user saw (ADR-0039).
        # Buffered: the model's text, which OUTPUT and DATA check before release_answer streams it.
        "draft_answer": text if buffered else released,
        "shown_answer": None if buffered else released,
        "guard_events": ctx.guard_records(),
        "tokens_in": (state.get("tokens_in") or 0) + tokens_in,
        "tokens_out": (state.get("tokens_out") or 0) + tokens_out,
    }
