"""The whole-turn deadline (timeout budget PR, scope D, I2; ADR-0255; the PR #12 review, decisions
5, 8 and 10; ADR-0264, ADR-0268).

The finding (docs/BACKLOG.md, High): nothing bounded a chat turn end to end; the gateway's 60 s cut
only what reached it. Now a turn runs under config/turn.yaml `deadline_s`, opened when its request
arrives, every bound below it is cut to what remains, and at the deadline the turn ends
`turn_timeout` through the terminal-error path, the conversation's lock released and every task
the turn owns cancelled.

With a test deadline of 3 s (answer_reserve_s 0, so a hung capability runs to the deadline): a hung
capability (probe waiting on its gate), a hung answer (the model provider never answers) and a
trickling answer (a token every 2 s, tests/fixtures/slow_upstream.py behind LiteLLM's `slow-*`
aliases) each end `turn_timeout` by the deadline; the next turn of the same conversation is not
refused 409; another user's turns, run one after another from half a second before the deadline
until well past its drain, each take their normal time; the event loop never lags 100 ms. With
answer_reserve_s > 0, a capability is cut by the reserve. A capability that ignores its
cancellation is abandoned after the drain's grace, logged and counted, and the turn still ends.
Ownership and the lock are taken within admission_s: a hung database read there is 503
`service_busy` within it. Timings are printed for docs/PROGRESS.md (rule 11).

A hung capability ends at 3 ± 0.5 s. A hung or trickling answer ends up to LITELLM_MARGIN_S
earlier: each model request tells LiteLLM to give up 1 s before the call's deadline (I4,
ADR-0257), which, for the answer, is the turn's; LiteLLM does, and the turn ends `turn_timeout`
then, with less than that second left (ADR-0258)."""

import asyncio
import dataclasses
import time
import uuid

import httpx
import pytest

from app.api.runner import TurnRunner
from app.api.schemas import TurnRequest
from app.context.tokens import default_counter
from app.identity import Principal
from app.models.gateway import LITELLM_MARGIN_S, ModelGateway
from app.resilience import deadline
from app.resilience.turn import TurnConfig
from tests.fixtures.capabilities.probe import graph as probe_graph
from tests.fixtures.slow_upstream import SlowUpstream
from tests.fixtures.timing import LagProbe

pytestmark = pytest.mark.integration

DEADLINE_S = 3.0
TOLERANCE_S = 0.5
LAG_BOUND_S = 0.1
OTHER_MARGIN_S = 1.0  # another user's turn may take this much longer than alone, at most
PROBE_MESSAGE = "مسبار: ما حالة الطلب؟"  # routed to probe by its keyword


@pytest.fixture(scope="module")
def upstream():
    server = SlowUpstream()
    server.start()
    yield server
    server.stop()


def _turn_config(**overrides) -> TurnConfig:
    values = dict(
        deadline_s=DEADLINE_S, answer_reserve_s=0.0, post_answer_reserve_s=0.0, persist_s=0.5,
        evidence_fetch_s=1.0, gateway_margin_s=1.0,
    )  # fmt: skip
    return TurnConfig(**(values | overrides))


def _timed(services, *, answer_alias: str | None = None, **turn):
    """`services` with the test deadline and, when given, the answer's tier on `answer_alias`."""
    config = _turn_config(**turn)
    if answer_alias is None:
        return dataclasses.replace(services, turn=config)
    tiers = dict(services.models_config.tiers)
    tiers["standard"] = tiers["standard"].model_copy(update={"alias": answer_alias})
    models = services.models_config.model_copy(update={"tiers": tiers})
    gateway = ModelGateway(services.settings, models, default_counter())
    return dataclasses.replace(services, turn=config, models_config=models, gateway=gateway)


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


async def _others_across(services, other: Principal, *, start_s: float, until_s: float) -> list[float]:
    """Another user's turns, one after another, from `start_s` after now until `until_s`: across the
    deadline's expiry, the cancellation of the turn's tasks and their drain."""
    started = time.monotonic()
    await asyncio.sleep(start_s)
    took: list[float] = []
    while time.monotonic() - started < until_s:
        events, seconds = await _turn(services, _request(other, "ما عاصمة مصر؟"))
        assert events[-1].type == "done", events[-1]
        took.append(seconds)
    return took


async def _no_waiters(gate: asyncio.Event, within_s: float = 1.0) -> bool:
    end = time.monotonic() + within_s
    while gate._waiters and time.monotonic() < end:
        await asyncio.sleep(0.02)
    return not gate._waiters


@pytest.mark.parametrize("case", ["hung_capability", "hung_answer", "trickling_answer"])
async def test_a_turn_past_its_deadline_ends_turn_timeout_and_frees_its_conversation(
    probe_services, upstream, litellm, case
):
    base = probe_services
    if case == "hung_capability":
        timed = _timed(base)
        user = _user("dl-cap", "cap:probe")
        message = PROBE_MESSAGE
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
            others = await _others_across(base, other, start_s=DEADLINE_S - 0.5, until_s=DEADLINE_S + 1.5)
            events, took = await mine
    finally:
        gate, probe_graph.GATE = probe_graph.GATE, None
    assert events[-1].type == "error" and events[-1].data.code == "turn_timeout", events[-1]
    earliest = DEADLINE_S - TOLERANCE_S - (0 if case == "hung_capability" else LITELLM_MARGIN_S)
    assert earliest <= took <= DEADLINE_S + TOLERANCE_S, took
    assert others and max(others) < alone + OTHER_MARGIN_S, (others, alone)
    assert probe.worst < LAG_BOUND_S, probe.worst
    # Every task the turn owned was cancelled: nothing waits on the gate, nothing is open upstream.
    if gate is not None:
        assert await _no_waiters(gate), "the capability's task still waits after the turn ended"
    upstream_closed = await upstream.settled("hung" if case == "hung_answer" else "trickle", within_s=2.0)
    # The lock was released: the next turn of the same conversation runs (not 409 turn_in_flight).
    after, after_took = await _turn(base, _request(user, "شكرًا", conversation))
    assert after[-1].type == "done", after[-1]
    print(
        f"D {case}: turn_timeout at {took:.2f} s (deadline {DEADLINE_S:.0f} s, from {earliest:.1f} to "
        f"{DEADLINE_S + TOLERANCE_S:.1f} s); another user's {len(others)} turns across the expiry and the "
        f"drain {min(others):.2f}–{max(others):.2f} s ({alone:.2f} s alone); loop lag max "
        f"{probe.worst * 1000:.1f} ms; upstream closed {upstream_closed:.2f} s after; next turn in the "
        f"conversation done in {after_took:.2f} s"
    )


async def test_a_capability_cut_by_the_answer_reserve_ends_turn_timeout(probe_services, litellm):
    """answer_reserve_s > 0: the capability runs under what remains − the reserve (ADR-0255)."""
    deadline_s, reserve_s = 4.0, 1.5
    timed = _timed(probe_services, deadline_s=deadline_s, answer_reserve_s=reserve_s,
                   post_answer_reserve_s=0.5,
                   persist_s=0.2)  # fmt: skip
    user = _user("dl-reserve", "cap:probe")
    conversation = str(uuid.uuid4())
    probe_graph.GATE = asyncio.Event()
    try:
        events, took = await _turn(timed, _request(user, PROBE_MESSAGE, conversation))
    finally:
        gate, probe_graph.GATE = probe_graph.GATE, None
    assert events[-1].type == "error" and events[-1].data.code == "turn_timeout", events[-1]
    cut = deadline_s - reserve_s
    assert cut - TOLERANCE_S <= took <= cut + TOLERANCE_S, took
    assert await _no_waiters(gate)
    after, _ = await _turn(probe_services, _request(user, "شكرًا", conversation))
    assert after[-1].type == "done", after[-1]
    print(
        f"D reserve: a hung capability cut by the {reserve_s} s answer reserve at {took:.2f} s "
        f"(bound {cut:.1f} s)"
    )


async def test_a_capability_that_ignores_its_cancellation_is_abandoned_and_the_turn_still_ends(
    probe_services, litellm, caplog
):
    """Decision 8 (ADR-0268): drain gives up after DRAIN_GRACE_S, logs task_abandoned with the node
    and counts it; the turn ends and another user's turns are on time meanwhile."""
    timed = _timed(probe_services)
    user, other = _user("dl-stubborn", "cap:probe"), _user("dl-stubborn-other")
    _, alone = await _turn(probe_services, _request(other, "ما عاصمة فرنسا؟"))
    before = deadline.ABANDONED["run_capabilities:probe"]
    probe_graph.STUBBORN_S = 8.0
    try:
        with LagProbe() as probe:
            mine = asyncio.create_task(_turn(timed, _request(user, PROBE_MESSAGE)))
            others = await _others_across(
                probe_services,
                other,
                start_s=DEADLINE_S - 0.5,
                until_s=DEADLINE_S + deadline.DRAIN_GRACE_S + 1.0,
            )
            events, took = await mine
    finally:
        probe_graph.STUBBORN_S = None
    assert events[-1].type == "error" and events[-1].data.code == "turn_timeout", events[-1]
    assert took <= DEADLINE_S + deadline.DRAIN_GRACE_S + TOLERANCE_S, took
    assert deadline.ABANDONED["run_capabilities:probe"] == before + 1
    assert "task_abandoned" in caplog.text and "run_capabilities:probe" in caplog.text
    assert others and max(others) < alone + OTHER_MARGIN_S, (others, alone)
    assert probe.worst < LAG_BOUND_S, probe.worst
    print(
        f"8 a capability ignoring its cancellation: turn_timeout at {took:.2f} s, task_abandoned counted; "
        f"another user's {len(others)} turns {min(others):.2f}–{max(others):.2f} s ({alone:.2f} s alone); "
        f"loop lag max {probe.worst * 1000:.1f} ms"
    )


class _HungOwners:
    """The conversation registry with its ownership read hung in the database (pg_sleep)."""

    def __init__(self, owners, db):
        self._owners, self._db = owners, db

    def __getattr__(self, name):
        return getattr(self._owners, name)

    async def bind_or_get(self, *args, **kwargs):
        async with self._db.connection() as conn:
            await conn.execute("SELECT pg_sleep(30)")
        return await self._owners.bind_or_get(*args, **kwargs)


async def test_a_hung_ownership_read_is_service_busy_within_the_admission_bound(services, litellm):
    """Decision 5 (ADR-0264): the deadline opens on arrival; ownership and the lock take at most
    admission_s; the slot is released and the database connection given back."""
    from app.main import create_app

    admission_s = 1.0
    hung = dataclasses.replace(
        services,
        turn=_turn_config(admission_s=admission_s),
        owners=_HungOwners(services.owners, services.db),
        turn_limits=services.turn.limiter(services.settings.lock_pool_size),
    )
    principal = {
        "user_id": f"u-dl-claim-{uuid.uuid4().hex[:6]}",
        "tenant_id": "default",
        "permissions": ["cap:general"],
    }
    app = create_app(start_background=False, services=hung)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as client:
        body = {
            "conversation_id": str(uuid.uuid4()),
            "principal": principal,
            "message": "سؤال",
            "locale": "ar",
        }
        started = time.monotonic()
        response = await client.post(
            "/v1/turns", json=body, headers={"X-Internal-Token": "test-internal-token"}
        )
        took = time.monotonic() - started
    assert response.status_code == 503 and response.json()["error"]["code"] == "service_busy"
    assert admission_s - 0.1 <= took <= admission_s + TOLERANCE_S, took
    assert hung.turn_limits.in_flight() == 0
    stats = services.db.pool.get_stats()
    assert stats.get("requests_waiting", 0) == 0, stats
    print(f"5 a hung ownership read: 503 service_busy at {took:.2f} s (admission bound {admission_s:.0f} s)")
