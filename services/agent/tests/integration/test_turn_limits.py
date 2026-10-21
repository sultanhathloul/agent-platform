"""Turn limits per user, per tenant and platform-wide (the PR #12 review, decision 1; ADR-0262;
CLAUDE.md rule 12a).

The finding: nothing limited one user's turns but the platform's lock pool (LOCK_POOL_SIZE, 20),
so one user with 20 conversations held every turn slot and every other user was refused
`service_busy` for as long as those turns ran. Now each turn takes a slot in the agent before its
lock: config/turn.yaml `per_user_max` (2), `per_tenant_max` (none by default: the platform's
capacity), the platform's capacity (LOCK_POOL_SIZE); past one it is refused at once, 429
`too_many_turns` (503 `service_busy` when the platform is full).

The held turns hang in their answer (tests/fixtures/slow_upstream.py behind LiteLLM's `slow-hung`
alias) until a 4 s test deadline. Timings are printed for docs/PROGRESS.md (rule 11)."""

import asyncio
import dataclasses
import time
import uuid

import httpx
import pytest

from app.api.runner import TurnRefused, TurnRunner
from app.api.schemas import TurnRequest
from app.context.tokens import default_counter
from app.identity import Principal
from app.models.gateway import ModelGateway
from app.resilience.limits import Caps, ConcurrencyLimiter
from app.resilience.turn import TurnConfig
from tests.fixtures.slow_upstream import SlowUpstream
from tests.fixtures.timing import LagProbe

pytestmark = pytest.mark.integration

DEADLINE_S = 4.0
LAG_BOUND_S = 0.1
OTHER_MARGIN_S = 1.0
REFUSED_AT_ONCE_S = 0.5  # a refusal waits for nothing: no lock, no queue
TURN = TurnConfig(
    deadline_s=DEADLINE_S, answer_reserve_s=0.0, post_answer_reserve_s=0.0, evidence_fetch_s=1.0,
    gateway_margin_s=1.0, persist_s=0.5,
)  # fmt: skip


@pytest.fixture(scope="module")
def upstream():
    server = SlowUpstream()
    server.start()
    yield server
    server.stop()


def _hung(services, limiter: ConcurrencyLimiter):
    """`services` with the answer's tier on `slow-hung`, the test deadline and `limiter`."""
    tiers = dict(services.models_config.tiers)
    tiers["standard"] = tiers["standard"].model_copy(update={"alias": "slow-hung"})
    config = services.models_config.model_copy(update={"tiers": tiers})
    gateway = ModelGateway(services.settings, config, default_counter())
    return dataclasses.replace(
        services, turn=TURN, models_config=config, gateway=gateway, turn_limits=limiter
    )


def _user(prefix: str, tenant: str = "default") -> Principal:
    return Principal(
        user_id=f"u-{prefix}-{uuid.uuid4().hex[:6]}", tenant_id=tenant, permissions=("cap:general",)
    )


def _request(principal: Principal, message: str = "ما عاصمة المملكة العربية السعودية؟") -> TurnRequest:
    return TurnRequest(conversation_id=str(uuid.uuid4()), principal=principal, message=message, locale="ar")


async def _turn(services, principal: Principal) -> tuple[str, str, float, float]:
    """(ran | refused, the terminal event's or the refusal's code, seconds to admission or refusal,
    seconds in all)."""
    runner = TurnRunner(services)
    request = _request(principal)
    started = time.monotonic()
    try:
        admission = await runner.admit(request)
    except TurnRefused as refused:
        took = time.monotonic() - started
        return "refused", refused.code, took, took
    admitted = time.monotonic() - started
    events = [e async for e in runner.stream(request, f"req-{uuid.uuid4().hex[:6]}", admission=admission)]
    last = events[-1]
    return "ran", last.data.code if last.type == "error" else last.type, admitted, time.monotonic() - started


async def test_a_users_third_turn_is_refused_at_once_and_another_user_runs_meanwhile(
    services, upstream, litellm
):
    limiter = services.turn.limiter(services.settings.lock_pool_size)
    held = _hung(services, limiter)
    shared = dataclasses.replace(services, turn_limits=limiter)
    a, b = _user("lim-a"), _user("lim-b")
    _, _, _, alone = await _turn(shared, b)
    with LagProbe() as probe:
        mine = [asyncio.create_task(_turn(held, a)) for _ in range(20)]
        await asyncio.sleep(0.3)
        other = await _turn(shared, b)
        outcomes = await asyncio.gather(*mine)
    ran = [o for o in outcomes if o[0] == "ran"]
    refused = [o for o in outcomes if o[0] == "refused"]
    assert len(ran) == 2 and {o[1] for o in ran} == {"turn_timeout"}, outcomes
    assert len(refused) == 18 and {o[1] for o in refused} == {"too_many_turns"}, outcomes
    slowest_refusal = max(o[2] for o in refused)
    assert slowest_refusal < REFUSED_AT_ONCE_S, slowest_refusal
    assert other[:2] == ("ran", "done"), other
    assert other[2] < 1.0, other  # admitted within 1 s
    assert other[3] < alone + OTHER_MARGIN_S, (other, alone)
    assert probe.worst < LAG_BOUND_S, probe.worst
    assert limiter.in_flight() == 0
    print(
        f"1 turn limits: A's 20 hung turns, 2 ran (turn_timeout), 18 refused too_many_turns, the slowest "
        f"in {slowest_refusal * 1000:.0f} ms; B admitted in {other[2]:.2f} s, done in {other[3]:.2f} s "
        f"({alone:.2f} s alone); loop lag max {probe.worst * 1000:.1f} ms"
    )


async def test_a_tenant_at_its_share_leaves_another_tenant_unaffected(services, upstream, litellm):
    limiter = ConcurrencyLimiter(Caps(per_user=2, per_tenant=10, platform=services.settings.lock_pool_size))
    held = _hung(services, limiter)
    shared = dataclasses.replace(services, turn_limits=limiter)
    x_users = [_user(f"lim-x{i}", tenant="tenant-x") for i in range(6)]
    y = _user("lim-y", tenant="tenant-y")
    _, _, _, alone = await _turn(shared, y)
    holding = [asyncio.create_task(_turn(held, user)) for user in x_users[:5] for _ in range(2)]
    for _ in range(100):  # bounded polling: until tenant X holds its 10
        if limiter.in_flight("tenant-x") == 10:
            break
        await asyncio.sleep(0.02)
    assert limiter.in_flight("tenant-x") == 10
    eleventh = await _turn(held, x_users[5])
    other = await _turn(shared, y)
    outcomes = await asyncio.gather(*holding)
    assert eleventh[:2] == ("refused", "too_many_turns"), eleventh
    assert {o[:2] for o in outcomes} == {("ran", "turn_timeout")}, outcomes
    assert other[:2] == ("ran", "done") and other[3] < alone + OTHER_MARGIN_S, (other, alone)
    print(
        f"1 tenant share 10: tenant X's 11th turn refused in {eleventh[2] * 1000:.0f} ms; tenant Y's turn "
        f"done in {other[3]:.2f} s ({alone:.2f} s alone)"
    )


async def test_by_default_one_tenant_may_use_the_whole_pool(services, upstream, litellm):
    capacity = services.settings.lock_pool_size
    limiter = services.turn.limiter(capacity)
    assert limiter.caps.per_tenant is None  # config/turn.yaml: per_tenant_max null
    held = _hung(services, limiter)
    users = [_user(f"lim-w{i}", tenant="tenant-w") for i in range(capacity // 2)]
    holding = [asyncio.create_task(_turn(held, user)) for user in users for _ in range(2)]
    for _ in range(200):
        if limiter.in_flight("tenant-w") == capacity:
            break
        await asyncio.sleep(0.02)
    full = limiter.in_flight("tenant-w")
    next_one = await _turn(held, _user("lim-w-next", tenant="tenant-w"))
    outcomes = await asyncio.gather(*holding)
    assert full == capacity
    assert {o[:2] for o in outcomes} == {("ran", "turn_timeout")}, outcomes
    assert next_one[:2] == ("refused", "service_busy"), next_one  # the platform is full, not the tenant
    print(f"1 default: one tenant held all {capacity} turns; the next one refused service_busy")


async def test_the_route_answers_429_too_many_turns(services, litellm):
    from app.main import create_app

    principal = {
        "user_id": f"u-lim-http-{uuid.uuid4().hex[:6]}",
        "tenant_id": "default",
        "permissions": ["cap:general"],
    }
    limiter = services.turn.limiter(services.settings.lock_pool_size)
    slots = [limiter.try_acquire("default", principal["user_id"]) for _ in range(2)]
    app = create_app(start_background=False, services=dataclasses.replace(services, turn_limits=limiter))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as client:
        body = {
            "conversation_id": str(uuid.uuid4()),
            "principal": principal,
            "message": "سؤال",
            "locale": "ar",
        }
        response = await client.post(
            "/v1/turns", json=body, headers={"X-Internal-Token": "test-internal-token"}
        )
    for slot in slots:
        slot.release()
    assert response.status_code == 429
    error = response.json()["error"]
    assert error["code"] == "too_many_turns" and error["message_ar"] and error["message_en"]
