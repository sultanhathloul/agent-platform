"""A generation reset never counts against a healthy server (timeout budget PR, scope A; the
breaker suspicion of the PR #10 verification, docs/BACKLOG.md "Before the phase 06 build").

The suspicion, from reading app/mcp/client.py only: when one call finds its MCP session gone (I9,
the server restarted) while another call waits for a fresh session, the generation-reset path may
count breaker failures against a server that is up. Reproduced here before any change (T2).

A healthy server restarts (tests/fixtures/slow_mcp.py, RestartingMCPServer: every session
forgotten, nothing goes down) while 1, 4 and max_concurrency calls are in flight or waiting for a
session; and a restart lands while sessions are being initialised. 50 iterations each, with
seeded jitter on when each call starts, how long the server holds it and when the restart lands.
After every iteration the breaker has counted no failure and is closed.

Two scenarios added for the race of ADR-0272, reproduced at 2000 iterations of four calls (3
failures counted) and with the restart pinned to each step of a call (7 in 300 when it lands
while a call is held at the server): a restart that lands while every request is held is seen
by none of them, so the next generation is opened later, around the next restart, and a call's
I9 re-send then meets a second 404. `restart-during-a-read` lands each restart while one of the
four calls is held (300 iterations); `restart-again-at-the-resend` restarts the server a second
time when the first call after a 404 arrives (50 iterations)."""

import asyncio
import random
import time

import pytest

from app.mcp.client import MCPClient, schema_hash
from app.mcp.config import MCPConfig, resolve
from tests.fixtures.slow_mcp import SCHEMAS, RestartingMCPServer

pytestmark = pytest.mark.integration

LABEL = {"ar": "أداة اختبار", "en": "Test tool"}
MAX_CONCURRENCY = 8
ITERATIONS = 50
READ_ITERATIONS = 300  # 7 failures counted in 300 before the fix (ADR-0272)
SEED = 20261021


def _config() -> MCPConfig:
    tools = [
        {"name": name, "remote_name": name, "risk": "read", "label": LABEL,
         "input_schema_sha256": schema_hash(SCHEMAS[name])}
        for name in ("slow", "quick")
    ]  # fmt: skip
    return MCPConfig.model_validate(
        {"servers": [{"id": "fx", "url_env": "URL", "internal": True, "profiles": ["default"],
                      "timeout_s": 10.0, "max_concurrency": MAX_CONCURRENCY, "tools": tools}]}
    )  # fmt: skip


@pytest.fixture
async def restarting():
    server = RestartingMCPServer()
    server.start()
    client = MCPClient(
        resolve(_config(), profile="default", allow_external=False, environ={"URL": server.url}),
        hash_key="test-content-hash-key",
        client_version="test",
    )
    client.start()
    assert await client.wait_connected(10), client.health()
    try:
        yield server, client.servers["fx"]
    finally:
        await client.stop()
        server.stop()


async def _iteration(
    server, connection, rng: random.Random, calls: int, *, during_initialize: bool, mode: str
):
    slow = connection.entry.tool("slow")

    async def one(delay: float, hold: float):
        await asyncio.sleep(delay)
        return await connection.call(slow, {"seconds": hold})

    async def restart(at: float | None):
        if at is not None:
            await asyncio.sleep(at)
            server.restart()

    if mode == "during-a-read":
        # The restart lands while one of the calls is held at the server, at a seeded point of its
        # hold: no request sees it until a later one.
        server.init_hold_s = 0.0
        server.restart_during_call(rng.randint(1, calls), rng.uniform(0.1, 0.9))
        restart_at = None
    elif during_initialize:
        # Every call needs a session of its own beyond the pooled ones; each initialize is held,
        # and the restart lands while they are held: the session ids they answer are forgotten.
        server.init_hold_s = 0.15
        restart_at = rng.uniform(0.02, 0.12)
    else:
        server.init_hold_s = 0.0
        restart_at = rng.uniform(0.0, 0.15)
    server.restart_at_resend = mode == "again-at-the-resend"
    tasks = [one(rng.uniform(0.0, 0.2), rng.uniform(0.0, 0.15)) for _ in range(calls)]
    outcomes = await asyncio.gather(*tasks, restart(restart_at), return_exceptions=True)
    server.init_hold_s = 0.0
    server.disarm()
    return outcomes[:calls]


@pytest.mark.parametrize(
    ("calls", "during_initialize", "mode", "iterations"),
    [
        (1, False, "timed", ITERATIONS),
        (4, False, "timed", ITERATIONS),
        (MAX_CONCURRENCY, False, "timed", ITERATIONS),
        (2 * MAX_CONCURRENCY, False, "timed", ITERATIONS),
        (MAX_CONCURRENCY, True, "timed", ITERATIONS),
        (4, False, "during-a-read", READ_ITERATIONS),
        (4, False, "again-at-the-resend", ITERATIONS),
    ],
    ids=[
        "one-call",
        "four-calls",
        "max-concurrency-calls",
        "calls-queued-for-a-slot",
        "reset-during-initialize",
        "restart-during-a-read",
        "restart-again-at-the-resend",
    ],
)
async def test_a_healthy_servers_restart_never_counts_a_breaker_failure(
    restarting, calls, during_initialize, mode, iterations
):
    server, connection = restarting
    # The first five scenarios keep their seeds of ADR-0249.
    seed = f"{SEED}-{calls}-{during_initialize}" + ("" if mode == "timed" else f"-{mode}")
    rng = random.Random(seed)
    # Every failure the breaker is told of, not only the count left at the end: a later success
    # resets the count, so a failure counted mid-iteration would not show in it.
    counted: list[int] = []
    breaker_failure = connection.breaker.failure

    def failure() -> None:
        counted.append(iteration)
        breaker_failure()

    connection.breaker.failure = failure
    failed_calls = 0
    started = time.monotonic()
    for iteration in range(iterations):
        outcomes = await _iteration(
            server, connection, rng, calls, during_initialize=during_initialize, mode=mode
        )
        failures = [o for o in outcomes if isinstance(o, BaseException)]
        failed_calls += len(failures)
        assert not counted, (
            iteration,
            len(counted),
            [getattr(f, "code", type(f).__name__) for f in failures],
        )
        assert connection.breaker.failures == 0, (iteration, connection.breaker.failures, failures)
        assert connection.breaker.state == "closed", (iteration, connection.breaker.state)
        # The next iteration starts on a connected server, as a later user's call would.
        await asyncio.wait_for(connection.connected.wait(), 10)
    print(
        f"scope A {calls} call(s), during_initialize={during_initialize}, {mode}: {iterations} iterations in "
        f"{time.monotonic() - started:.1f} s, {server.restarts} restarts, {server.not_found} 404s, "
        f"{connection.sessions} sessions; failed calls {failed_calls}, "
        f"breaker failures counted {len(counted)}"
    )
