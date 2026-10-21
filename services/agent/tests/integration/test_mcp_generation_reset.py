"""A generation reset never counts against a healthy server (timeout budget PR, scope A; the
breaker suspicion of the PR #10 verification, docs/BACKLOG.md "Before the phase 06 build").

The suspicion, from reading app/mcp/client.py only: when one call finds its MCP session gone (I9,
the server restarted) while another call waits for a fresh session, the generation-reset path may
count breaker failures against a server that is up. Reproduced here before any change (T2).

A healthy server restarts (tests/fixtures/slow_mcp.py, RestartingMCPServer: every session
forgotten, nothing goes down) while 1, 4 and max_concurrency calls are in flight or waiting for a
session; and a restart lands while sessions are being initialised. 50 iterations each, with
seeded jitter on when each call starts, how long the server holds it and when the restart lands.
After every iteration the breaker has counted no failure and is closed, and every call has
succeeded: a healthy server's restart is invisible to its callers."""

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


async def _iteration(server, connection, rng: random.Random, calls: int, *, during_initialize: bool):
    slow = connection.entry.tool("slow")

    async def one(delay: float, hold: float):
        await asyncio.sleep(delay)
        return await connection.call(slow, {"seconds": hold})

    async def restart(at: float):
        await asyncio.sleep(at)
        server.restart()

    if during_initialize:
        # Every call needs a session of its own beyond the pooled ones; each initialize is held,
        # and the restart lands while they are held: the session ids they answer are forgotten.
        server.init_hold_s = 0.15
        restart_at = rng.uniform(0.02, 0.12)
    else:
        server.init_hold_s = 0.0
        restart_at = rng.uniform(0.0, 0.15)
    tasks = [one(rng.uniform(0.0, 0.2), rng.uniform(0.0, 0.15)) for _ in range(calls)]
    outcomes = await asyncio.gather(*tasks, restart(restart_at), return_exceptions=True)
    server.init_hold_s = 0.0
    return outcomes[:calls]


@pytest.mark.parametrize(
    ("calls", "during_initialize"),
    [(1, False), (4, False), (MAX_CONCURRENCY, False), (2 * MAX_CONCURRENCY, False), (MAX_CONCURRENCY, True)],
    ids=["one-call", "four-calls", "max-concurrency-calls", "calls-queued-for-a-slot", "reset-during-initialize"],
)
async def test_a_healthy_servers_restart_never_counts_a_breaker_failure(restarting, calls, during_initialize):
    server, connection = restarting
    rng = random.Random(f"{SEED}-{calls}-{during_initialize}")
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
    for iteration in range(ITERATIONS):
        outcomes = await _iteration(server, connection, rng, calls, during_initialize=during_initialize)
        failures = [o for o in outcomes if isinstance(o, BaseException)]
        failed_calls += len(failures)
        assert not counted, (iteration, len(counted), [getattr(f, "code", type(f).__name__) for f in failures])
        assert connection.breaker.failures == 0, (iteration, connection.breaker.failures, failures)
        assert connection.breaker.state == "closed", (iteration, connection.breaker.state)
        # The next iteration starts on a connected server, as a later user's call would.
        await asyncio.wait_for(connection.connected.wait(), 10)
    print(
        f"scope A {calls} call(s), during_initialize={during_initialize}: {ITERATIONS} iterations in "
        f"{time.monotonic() - started:.1f} s, {server.restarts} restarts, {server.not_found} 404s, "
        f"{connection.sessions} sessions; failed calls {failed_calls}, breaker failures counted {len(counted)}"
    )
