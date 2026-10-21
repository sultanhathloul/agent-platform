"""The whole-turn deadline (timeout budget PR, scope D, I2; ADR-0255).

The finding (docs/BACKLOG.md, High): nothing bounded a chat turn end to end; the gateway's 60 s
cut only what reached it. Now a turn runs under config/turn.yaml `deadline_s`, every bound below it
is cut to what remains, and at the deadline the turn ends `turn_timeout` through the terminal-error
path, the conversation's lock released and every task the turn owns cancelled.

With a test deadline of 3 s (answer_reserve_s 0, so a hung capability runs to the deadline): a hung
capability (probe waiting on its gate), a hung answer (the model provider never answers) and a
trickling answer (a token every 2 s, tests/fixtures/slow_upstream.py behind LiteLLM's `slow-*`
aliases) each end `turn_timeout` by the deadline; the next turn of the same conversation is not
refused 409; another user's turn meanwhile takes its normal time; the event loop never lags
100 ms. Timings are printed for docs/PROGRESS.md (rule 11).

A hung capability ends at 3 ± 0.5 s. A hung or trickling answer ends up to LITELLM_MARGIN_S
earlier: each model request tells LiteLLM to give up 1 s before the call's deadline (I4,
ADR-0257), which, for the answer, is the turn's; LiteLLM does, and the turn ends `turn_timeout`
then, with less than that second left (ADR-0258)."""

import asyncio
import dataclasses
import time
import uuid

import pytest

from app.api.runner import TurnRunner
from app.api.schemas import TurnRequest
from app.context.tokens import default_counter
from app.identity import Principal
from app.models.gateway import LITELLM_MARGIN_S, ModelGateway
from app.resilience.turn import TurnConfig
from tests.fixtures.capabilities.probe import graph as probe_graph
from tests.fixtures.slow_upstream import SlowUpstream
from tests.fixtures.timing import LagProbe

pytestmark = pytest.mark.integration

DEADLINE_S = 3.0
TOLERANCE_S = 0.5
LAG_BOUND_S = 0.1
OTHER_MARGIN_S = 1.0  # another user's turn may take this much longer than alone, at most


@pytest.fixture(scope="module")
def upstream():
    server = SlowUpstream()
    server.start()
    yield server
    server.stop()


def _timed(services, *, answer_alias: str | None = None):
    """`services` with the 3 s test deadline and, when given, the answer's tier on `answer_alias`."""
    turn = TurnConfig(deadline_s=DEADLINE_S, answer_reserve_s=0.0, evidence_fetch_s=1.0, gateway_margin_s=1.0)
    if answer_alias is None:
        return dataclasses.replace(services, turn=turn)
    tiers = dict(services.models_config.tiers)
    tiers["standard"] = tiers["standard"].model_copy(update={"alias": answer_alias})
    config = services.models_config.model_copy(update={"tiers": tiers})
    gateway = ModelGateway(services.settings, config, default_counter())
    return dataclasses.replace(services, turn=turn, models_config=config, gateway=gateway)


def _request(principal: Principal, message: str, conversation: str | None = None) -> TurnRequest:
    return TurnRequest(
        conversation_id=conversation or str(uuid.uuid4()), principal=principal, message=message, locale="ar"
    )


async def _turn(services, request: TurnRequest) -> tuple[list, float]:
    started = time.monotonic()
    events = [e async for e in TurnRunner(services).stream(request, f"req-{uuid.uuid4().hex[:6]}")]
    return events, time.monotonic() - started


def _user(prefix: str, *permissions: str) -> Principal:
    return Principal(user_id=f"u-{prefix}-{uuid.uuid4().hex[:6]}", permissions=("cap:general", *permissions))


@pytest.mark.parametrize("case", ["hung_capability", "hung_answer", "trickling_answer"])
async def test_a_turn_past_its_deadline_ends_turn_timeout_and_frees_its_conversation(
    probe_services, upstream, litellm, case
):
    base = probe_services
    if case == "hung_capability":
        timed = _timed(base)
        user = _user("dl-cap", "cap:probe")
        message = "مسبار: ما حالة الطلب؟"  # routed to probe by its keyword
        probe_graph.GATE = asyncio.Event()  # never set: the capability waits on it
    else:
        timed = _timed(base, answer_alias="slow-hung" if case == "hung_answer" else "slow-trickle")
        user = _user("dl-ans")
        message = "ما عاصمة المملكة العربية السعودية؟"
    other = _user("dl-other")
    _, alone = await _turn(base, _request(other, "ما عاصمة فرنسا؟"))
    upstream.reset()
    conversation = str(uuid.uuid4())
    try:
        with LagProbe() as probe:
            mine = asyncio.create_task(_turn(timed, _request(user, message, conversation)))
            await asyncio.sleep(0.5)
            other_events, other_took = await _turn(base, _request(other, "ما عاصمة مصر؟"))
            events, took = await mine
    finally:
        gate, probe_graph.GATE = probe_graph.GATE, None
    assert events[-1].type == "error" and events[-1].data.code == "turn_timeout", events[-1]
    earliest = DEADLINE_S - TOLERANCE_S - (0 if case == "hung_capability" else LITELLM_MARGIN_S)
    assert earliest <= took <= DEADLINE_S + TOLERANCE_S, took
    assert other_events[-1].type == "done" and other_took < alone + OTHER_MARGIN_S, (other_took, alone)
    assert probe.worst < LAG_BOUND_S, probe.worst
    # Every task the turn owned was cancelled: nothing waits on the gate, nothing is open upstream.
    if gate is not None:
        await asyncio.sleep(0.1)
        assert not gate._waiters, "the capability's task still waits after the turn ended"
    upstream_closed = await upstream.settled("hung" if case == "hung_answer" else "trickle", within_s=2.0)
    # The lock was released: the next turn of the same conversation runs (not 409 turn_in_flight).
    after, after_took = await _turn(base, _request(user, "شكرًا", conversation))
    assert after[-1].type == "done", after[-1]
    print(
        f"D {case}: turn_timeout at {took:.2f} s (deadline {DEADLINE_S:.0f} s, from {earliest:.1f} to "
        f"{DEADLINE_S + TOLERANCE_S:.1f} s); another user's "
        f"turn {other_took:.2f} s ({alone:.2f} s alone); loop lag max {probe.worst * 1000:.1f} ms; upstream "
        f"closed {upstream_closed:.2f} s after; next turn in the conversation done in {after_took:.2f} s"
    )
