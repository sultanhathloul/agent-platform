"""The post-answer reserve (the PR #12 review, decision 2; ADR-0263).

The finding: the answer reserve covered the answer only. The guards on the whole answer, persistence,
the summary and memory ran in what remained, so a turn whose answer ended near the deadline was cut
after its last token: `turn_timeout` with no retract, the exchange possibly not persisted, the
whole-answer OUTPUT check skipped. Now config/turn.yaml `post_answer_reserve_s` is kept after the
answer: the answer runs under min(its ceiling, what remains − the reserve); the guards on the whole
answer end `persist_s` before the deadline (a check cut there follows its policy's on_error); each
checkpoint write takes at most `persist_s`; the summary and memory run only with their ceiling left,
else `summary_skipped` / `memory_skipped`; once the answer's last token is sent the turn never ends
`turn_timeout`.

Times: deadline 8 s, answer_reserve 4 s, post_answer_reserve 2 s, persist 0.5 s. The probe
capability takes until just before the answer's reserve; the answer then streams for about 1.8 s
and ends near its bound (6 s). The model's text is replaced after the real LiteLLM call (ADR-0075)
and replayed to the stream at a set pace."""

import asyncio
import dataclasses
import time
import uuid

import pytest

from app.api.runner import TurnRunner
from app.api.schemas import TurnRequest
from app.graph.nodes import assemble as assemble_node
from app.identity import Principal
from app.models.gateway import ModelUnavailable
from app.resilience import deadline
from app.resilience.turn import TurnConfig
from tests.fixtures.capabilities.probe import graph as probe_graph
from tests.fixtures.replace import ReplacingGateway

pytestmark = pytest.mark.integration

DEADLINE_S, ANSWER_RESERVE_S, POST_S, PERSIST_S = 8.0, 4.0, 2.0, 0.5
TURN = TurnConfig(
    deadline_s=DEADLINE_S, answer_reserve_s=ANSWER_RESERVE_S, post_answer_reserve_s=POST_S,
    persist_s=PERSIST_S,
    evidence_fetch_s=1.0, gateway_margin_s=1.0,
)  # fmt: skip
CAPABILITY_S = 3.3  # the probe ends just before the answer's reserve begins (8 − 4 = 4 s)
PACE_S = 0.45  # four chunks of the reply: the answer ends near its bound (8 − 2 = 6 s)
SPLIT_NUMBER = (
    "رقمي 050123\n4567 للتجربة."  # no sentence holds the number whole: the whole-answer pass catches it
)
PROBE_MESSAGE = "مسبار: ما حالة الطلب؟"


class PacedGateway(ReplacingGateway):
    """Replaces the answer's text after the real call and streams it at PACE_S a chunk. A judge call,
    with `judge_ceiling_s`, makes its real call and then never gets its verdict: it ends as the
    model gateway ends a call, ModelUnavailable at its own ceiling, or the deadline around it."""

    def __init__(self, inner, replies, *, judge_ceiling_s: float | None = None):
        super().__init__(inner, replies, chunk=7)
        self.judge_ceiling_s = judge_ceiling_s

    async def call(self, decision, package, *, on_token=None, temperature=None):
        if self.judge_ceiling_s is not None and decision.role == "judge":
            self.calls.append("judge")
            await self._inner.call(decision, package, temperature=temperature)
            try:
                with deadline.within(self.judge_ceiling_s):
                    await asyncio.Event().wait()  # the verdict never arrives
            except deadline.DeadlineExceeded as exc:
                if exc.inherited:
                    raise
                raise ModelUnavailable("judge past its ceiling") from None
        text = self._next(decision.role)
        if text is None or on_token is None:
            return await super().call(decision, package, on_token=on_token, temperature=temperature)
        self.calls.append(decision.role)
        result = await self._inner.call(decision, package, on_token=None, temperature=temperature)
        for start in range(0, len(text), self._chunk):
            on_token(text[start : start + self._chunk])
            await asyncio.sleep(PACE_S)
        return result.model_copy(update={"text": text})


def _paced(services, reply: str = SPLIT_NUMBER, **kwargs):
    gateway = PacedGateway(services.gateway, {"reasoning": reply}, **kwargs)
    return dataclasses.replace(services, turn=TURN, gateway=gateway), gateway


def _principal(role: str | None = None) -> Principal:
    return Principal(
        user_id=f"u-post-{uuid.uuid4().hex[:6]}",
        permissions=("cap:general", "cap:probe"),
        policy_roles=(f"policy:{role}",) if role else (),
    )


async def _turn(services, principal: Principal, conversation: str | None = None):
    request = TurnRequest(
        conversation_id=conversation or str(uuid.uuid4()),
        principal=principal,
        message=PROBE_MESSAGE,
        locale="ar",
    )
    started = time.monotonic()
    events = [e async for e in TurnRunner(services).stream(request, f"req-{uuid.uuid4().hex[:6]}")]
    return events, time.monotonic() - started, request.conversation_id


def _token_end(events) -> int:
    return max(i for i, e in enumerate(events) if e.type == "token")


@pytest.fixture
def slow_capability():
    probe_graph.SLEEP_S = CAPABILITY_S
    yield
    probe_graph.SLEEP_S = None


async def test_an_answer_near_its_bound_is_persisted_checked_whole_and_retracted_never_timed_out(
    probe_services, spans, litellm, slow_capability
):
    services, gateway = _paced(probe_services)
    events, took, conversation = await _turn(services, _principal())
    types = [e.type for e in events]
    assert events[-1].type == "done", events[-1]
    assert "turn_timeout" not in [getattr(e.data, "code", None) for e in events]
    # The answer ended near its bound: after the reserve opened, before the post-answer reserve.
    answer = next(s for s in spans.get_finished_spans() if s.name == "node.answer")
    answer_end_s = (answer.end_time - answer.start_time) / 1e9
    assert answer_end_s > 1.5, answer_end_s
    # The whole-answer pass ran and retracted what the sentence buffer let through (ADR-0111).
    assert "node.guard.OUTPUT" in {s.name for s in spans.get_finished_spans()}
    retract = types.index("retract")
    assert retract > _token_end(events[:retract]) and events[retract].data.reason == "answer_redacted"
    # The exchange is persisted, with the text the user ends with.
    stored = (await services.graph.aget_state({"configurable": {"thread_id": conversation}})).values[
        "messages"
    ]
    assert stored[-1].content == events[-1].data.answer and "[REDACTED:SA_MOBILE]" in stored[-1].content
    assert took <= DEADLINE_S, took
    # No room for memory's extractor: skipped and said so.
    memory = next(s for s in spans.get_finished_spans() if s.name == "node.write_memory")
    assert memory.attributes["memory.write.outcome"] == "skipped"
    assert "memory_skipped" in [e.name for e in memory.events]
    print(
        f"2 post-answer: answer node {answer_end_s:.2f} s, turn done at {took:.2f} s "
        f"(deadline {DEADLINE_S:.0f} s): "
        f"retracted and re-sent redacted, persisted, memory skipped; no turn_timeout"
    )


@pytest.mark.parametrize(
    ("cut", "on_error"), [("ceiling", "open"), ("ceiling", "closed"), ("horizon", "open")]
)
async def test_a_judge_hung_at_output_follows_its_policys_on_error(
    probe_services, role, litellm, slow_capability, cut, on_error
):
    """A judge that never answers, cut by its own ceiling (1 s) or by the guards' horizon
    (persist_s before the deadline): its policy's on_error decides, never turn_timeout. Cut at the
    horizon it leaves the DATA pass after it no time, whose fail-closed checks then end the turn
    guard_unavailable (their on_error); the exchange's write keeps its persist_s either way."""
    name, add = role
    await add(
        checkpoints=["OUTPUT"],
        checker="llm_judge",
        config={"topic_description": "أي موضوع", "min_confidence": 0.6},
        action="block",
        on_error=on_error,
        message_ar="حُجبت الإجابة.",
        message_en="Withheld.",
    )
    ceiling = 1.0 if cut == "ceiling" else 20.0
    services, gateway = _paced(probe_services, "إجابة سليمة للتجربة.", judge_ceiling_s=ceiling)
    events, took, _ = await _turn(services, _principal(name))
    assert "judge" in gateway.calls
    assert "turn_timeout" not in [getattr(e.data, "code", None) for e in events]
    judged = [
        g
        for g in events[-1].data.guard_audit
        if g.checkpoint == "OUTPUT" and g.policy_id and g.policy_id.startswith(name)
    ]
    assert judged and all("checker error" in (g.detail or "") for g in judged), judged
    assert all(f"on_error={on_error}" in g.detail for g in judged), judged
    if cut == "horizon":
        assert all("DeadlineExceeded" in g.detail for g in judged), judged
        assert events[-1].type == "error" and events[-1].data.code == "guard_unavailable", events[-1]
    elif on_error == "open":
        assert events[-1].type == "done", events[-1]
    else:
        assert events[-1].type == "error" and events[-1].data.code in ("output_blocked", "guard_unavailable")
    # The check ended persist_s before the deadline at the latest, so the turn did not run past it.
    assert took <= DEADLINE_S + 0.5, took
    print(f"2 judge hung at OUTPUT, cut by its {cut}, on_error={on_error}: {events[-1].type} "
          f"{getattr(events[-1].data, 'code', '')} at {took:.2f} s; no turn_timeout")  # fmt: skip


async def test_the_summary_and_memory_are_skipped_when_no_time_remains(
    probe_services, spans, litellm, slow_capability, monkeypatch
):
    real = assemble_node._plan

    def folding(state, ctx):
        plan = real(state, ctx)
        messages = list(state.get("messages") or [])
        return dataclasses.replace(plan, fold=messages[:2]) if messages else plan

    monkeypatch.setattr(assemble_node, "_plan", folding)
    services, _ = _paced(probe_services, "إجابة سليمة للتجربة.")
    events, took, _ = await _turn(services, _principal())
    assert events[-1].type == "done", events[-1]
    finished = {s.name: s for s in spans.get_finished_spans()}
    assert "summary_skipped" in [e.name for e in finished["node.fold_summary"].events]
    assert "memory_skipped" in [e.name for e in finished["node.write_memory"].events]
    print(f"2 skips: summary_skipped and memory_skipped, turn done at {took:.2f} s")
