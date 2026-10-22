"""Platform settings through the agent's admin API (owner decision 2026-10-22, items 3 to 9;
ADR-0276 to ADR-0284).

- The agent checks the admin role too: a call without the gateway's X-User-Admin is refused.
- A save is checked with the timeout budget's rules: an invalid pair is refused in plain words and
  nothing is written; an MCP server's max_concurrency above the file's is refused.
- A save, a restore and a revert each make a new version, with its actor; no row changes.
- A change applies to the next turn with no restart (per_user_max 2 → 1); a turn in flight keeps
  its slot.
- With Redis gone, a process keeps its last values and turns go on; it heals from Postgres.
- The usage counts move as turns start and end, and the usage endpoint answers within 50 ms with
  20 turns in flight.

The held turns hang in their answer (tests/fixtures/slow_upstream.py behind LiteLLM's `slow-hung`
alias) until a 4 s test deadline. Timings are printed for docs/PROGRESS.md (rule 11)."""

import asyncio
import dataclasses
import time
import uuid
from urllib.parse import quote

import psycopg
import pytest
from redis.asyncio import Redis

from app.api.locks import TurnLocks
from app.api.runner import TurnRefused, TurnRunner
from app.api.schemas import TurnRequest
from app.context.tokens import default_counter
from app.db import Database
from app.guardrails.store import Actor
from app.identity import Principal
from app.models.gateway import ModelGateway
from app.platform_settings.store import SettingsStore
from app.resilience.turn import TurnConfig
from tests.fixtures.slow_upstream import SlowUpstream

pytestmark = pytest.mark.integration

ADMIN = {
    "X-Internal-Token": "test-internal-token",
    "X-User-Id": "kc-admin-1",
    "X-User-Admin": "true",
    "X-User-Name": "admin",
    "X-User-Display-Name": quote("مديرة المنصة"),
}
DEADLINE_S = 4.0
USAGE_BOUND_S = 0.050
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


@pytest.fixture
async def restored(services):
    """Whatever a test saves, the files' values are in force again after it."""
    yield
    if services.platform.current.overrides:
        await services.platform.save({}, Actor("test", "test", "test"))


@pytest.fixture
async def pooled(services):
    """A lock pool of the test's own, closed after it (held turns open lock connections)."""
    locks = TurnLocks(services.settings.database_url, size=services.settings.lock_pool_size)
    await locks.open()
    yield dataclasses.replace(services, locks=locks)
    await locks.close()


def _hung(services):
    """`services` with the answer's tier on `slow-hung` and the test deadline; the session's turn
    limits, which the settings store resizes."""
    tiers = dict(services.models_config.tiers)
    tiers["standard"] = tiers["standard"].model_copy(update={"alias": "slow-hung"})
    config = services.models_config.model_copy(update={"tiers": tiers})
    gateway = ModelGateway(services.settings, config, default_counter())
    return dataclasses.replace(services, turn=TURN, models_config=config, gateway=gateway)


def _user(prefix: str) -> Principal:
    return Principal(user_id=f"u-{prefix}-{uuid.uuid4().hex[:6]}", tenant_id="default", permissions=("cap:general",))


def _request(principal: Principal) -> TurnRequest:
    return TurnRequest(
        conversation_id=str(uuid.uuid4()), principal=principal, message="ما عاصمة المملكة؟", locale="ar"
    )


async def _turn(services, principal: Principal) -> tuple[str, str]:
    runner = TurnRunner(services)
    request = _request(principal)
    try:
        admission = await runner.admit(request)
    except TurnRefused as refused:
        return "refused", refused.code
    events = [e async for e in runner.stream(request, f"req-{uuid.uuid4().hex[:6]}", admission=admission)]
    last = events[-1]
    return "ran", last.data.code if last.type == "error" else last.type


async def _until(predicate, timeout_s: float = 3.0) -> None:
    for _ in range(int(timeout_s / 0.02)):
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not reached")


async def _version_count(services) -> int:
    async with services.db.connection() as conn:
        return (await (await conn.execute("SELECT count(*) AS n FROM agent.platform_settings")).fetchone())["n"]


ENDPOINTS = [
    ("GET", "/v1/admin/settings", None),
    ("POST", "/v1/admin/settings/preview", {"overrides": {}}),
    ("POST", "/v1/admin/settings", {"overrides": {"turn.per_user_max": 1}}),
    ("POST", "/v1/admin/settings/revert", {"version": 1}),
    ("GET", "/v1/admin/settings/versions", None),
    ("GET", "/v1/admin/usage", None),
]


@pytest.mark.parametrize(("method", "path", "body"), ENDPOINTS)
@pytest.mark.parametrize("admin", [None, "false", "TRUE-ish"])
async def test_a_non_admin_is_refused_at_the_agent_too(client, services, method, path, body, admin):
    headers = {k: v for k, v in ADMIN.items() if k != "X-User-Admin"}
    if admin is not None:
        headers["X-User-Admin"] = admin
    before = await _version_count(services)
    answer = await client.request(method, path, json=body, headers=headers)
    assert answer.status_code == 403, answer.text
    assert answer.json()["error"]["code"] == "forbidden"
    assert await _version_count(services) == before
    wrong = await client.request(method, path, json=body, headers={**ADMIN, "X-Internal-Token": "nope"})
    assert wrong.status_code == 401


async def test_the_page_shows_defaults_values_read_only_values_and_the_tree(client, services):
    answer = await client.get("/v1/admin/settings", headers=ADMIN)
    assert answer.status_code == 200, answer.text
    view = answer.json()
    fields = {f["key"]: f for f in view["fields"]}
    assert fields["turn.per_user_max"] | {} == fields["turn.per_user_max"]
    assert (fields["turn.per_user_max"]["default"], fields["turn.per_user_max"]["value"]) == (2, 2)
    assert fields["turn.per_user_max"]["maximum"] == services.settings.lock_pool_size
    assert fields["mcp.rag.max_concurrency"]["maximum"] == 8 and fields["mcp.rag.max_concurrency"]["server"] == "rag"
    assert {"turns", "models", "uploads", "mcp"} == {f["group"] for f in view["fields"]}
    read_only = {r["key"]: r for r in view["read_only"]}
    assert read_only["lock_pool_size"]["value"] == services.settings.lock_pool_size
    assert read_only["mem_limit.ingest"]["value"] == 2048
    assert "docker-compose.yml" in read_only["mem_limit.ingest"]["source"]
    tree = {p["rule"]: p for p in view["tree"]}
    assert tree["answer-reserve-in-turn"]["holds"] and tree["answer-reserve-in-turn"]["outer_value"] == 120
    assert any(rule.startswith("capability-in-room:") for rule in tree)
    assert all(p["holds"] for p in view["tree"])


async def test_an_invalid_pair_is_refused_in_plain_words_and_nothing_changes(client, services):
    before, current = await _version_count(services), services.platform.current
    answer = await client.post(
        "/v1/admin/settings", json={"overrides": {"turn.answer_reserve_s": 130}}, headers=ADMIN
    )
    assert answer.status_code == 400, answer.text
    error = answer.json()["error"]
    assert error["code"] == "settings_invalid"
    assert "answer-reserve-in-turn" in error["details"]
    assert "مهلة الإجابة (130 ثانية) أطول من مهلة السؤال كله (120 ثانية)" in error["message_ar"]
    assert "130 s" in error["message_en"]
    assert await _version_count(services) == before
    assert services.platform.current is current


async def test_a_preview_shows_what_a_change_would_produce_and_writes_nothing(client, services):
    before = await _version_count(services)
    answer = await client.post(
        "/v1/admin/settings/preview", json={"overrides": {"turn.deadline_s": 150}}, headers=ADMIN
    )
    assert answer.status_code == 200, answer.text
    preview = answer.json()
    assert preview["valid"] and preview["problems"] == [] and preview["changed"] == ["turn.deadline_s"]
    assert {f["key"]: f["value"] for f in preview["fields"]}["turn.deadline_s"] == 150
    tree = {p["rule"]: p for p in preview["tree"]}
    assert tree["answer-reserve-in-turn"]["outer_value"] == 150
    bad = (
        await client.post(
            "/v1/admin/settings/preview", json={"overrides": {"turn.deadline_s": 80}}, headers=ADMIN
        )
    ).json()
    assert not bad["valid"]
    failing = {p["family"] for p in bad["tree"] if not p["holds"]}
    assert failing == {"capability-in-room", "reasoning-in-turn"}, failing  # 80 s: 90 s answering model too
    rooms = [p for p in bad["problems"] if p["family"] == "capability-in-room"]
    assert rooms and all("«" in p["message_ar"] for p in rooms)  # the capability's own name
    assert await _version_count(services) == before


async def test_an_mcp_max_concurrency_above_the_files_is_refused(client, services):
    before = await _version_count(services)
    answer = await client.post(
        "/v1/admin/settings", json={"overrides": {"mcp.rag.max_concurrency": 9}}, headers=ADMIN
    )
    assert answer.status_code == 400, answer.text
    assert answer.json()["error"]["details"] == ["range:mcp.rag.max_concurrency"]
    assert await _version_count(services) == before


async def test_save_restore_and_revert_each_make_a_new_version_and_no_row_changes(
    client, services, restored, database_url
):
    first = (
        await client.post("/v1/admin/settings", json={"overrides": {"turn.per_user_max": 1}}, headers=ADMIN)
    ).json()
    assert first["change"] == "save"
    assert first["changes"] == [{"key": "turn.per_user_max", "before": 2, "after": 1}]
    assert services.platform.current.version == first["version"]
    assert services.turn_limits.caps.per_user == 1
    restore = (await client.post("/v1/admin/settings", json={"overrides": {}}, headers=ADMIN)).json()
    assert (restore["change"], restore["version"]) == ("restore", first["version"] + 1)
    assert services.turn_limits.caps.per_user == 2
    revert = await client.post("/v1/admin/settings/revert", json={"version": first["version"]}, headers=ADMIN)
    assert revert.status_code == 201, revert.text
    revert = revert.json()
    assert (revert["change"], revert["reverted_to"], revert["version"]) == (
        "revert",
        first["version"],
        first["version"] + 2,
    )
    assert services.platform.current.overrides == {"turn.per_user_max": 1}
    versions = (await client.get("/v1/admin/settings/versions", headers=ADMIN)).json()["versions"]
    assert [v["version"] for v in versions[:3]] == [revert["version"], restore["version"], first["version"]]
    assert versions[0]["created_by_username"] == "admin" and versions[0]["created_by_name"] == "مديرة المنصة"
    assert versions[-1]["change"] == "seed" and versions[-1]["created_by"] == "seed"
    with psycopg.connect(database_url, autocommit=True) as conn:
        for statement in (
            "UPDATE agent.platform_settings SET content = '{}' WHERE version = %s",
            "DELETE FROM agent.platform_settings WHERE version = %s",
        ):
            with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
                conn.execute(statement, (first["version"],))
    stale = await client.post(
        "/v1/admin/settings",
        json={"overrides": {}, "expected_version": first["version"]},
        headers=ADMIN,
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "settings_conflict"


async def test_per_user_max_2_to_1_takes_effect_on_the_next_turn_with_no_restart(
    client, pooled, upstream, litellm, restored
):
    held = _hung(pooled)
    a = _user("set-a")
    first = asyncio.create_task(_turn(held, a))
    await _until(lambda: pooled.turn_limits.in_flight("default", a.user_id) == 1)
    saved = await client.post("/v1/admin/settings", json={"overrides": {"turn.per_user_max": 1}}, headers=ADMIN)
    assert saved.status_code == 201, saved.text
    started = time.monotonic()
    second = await _turn(held, a)
    refused_in = time.monotonic() - started
    assert second == ("refused", "too_many_turns"), second
    await client.post("/v1/admin/settings", json={"overrides": {}}, headers=ADMIN)
    third = asyncio.create_task(_turn(held, a))
    await _until(lambda: pooled.turn_limits.in_flight("default", a.user_id) == 2)
    assert await first == ("ran", "turn_timeout")  # the turn in flight kept its slot and its end
    assert await third == ("ran", "turn_timeout")
    assert pooled.turn_limits.in_flight() == 0
    print(f"9 per_user_max 2 → 1: the next turn refused too_many_turns in {refused_in * 1000:.0f} ms, no restart")


async def test_with_redis_gone_a_process_keeps_its_last_values_and_turns_go_on(services, litellm, restored):
    """A second process whose Redis is unreachable: it started from Postgres, misses the change
    notice, keeps the values it last knew, serves turns with them, and heals at its next reload."""
    dead = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2, socket_timeout=0.2)
    other = SettingsStore(services.db, services.platform.defaults, dead, namespace="dead", refresh_s=3600)
    await other.start(seed=False)
    try:
        known = other.current.version
        await services.platform.save({"turn.per_user_max": 1}, Actor("test", "test", "test"))
        await asyncio.sleep(0.3)
        assert other.current.version == known  # the notice never reached it: the last values stay
        cut_off = dataclasses.replace(services, platform=other)
        assert await _turn(cut_off, _user("set-redis")) == ("ran", "done")
        await other.load()  # its periodic reload from Postgres
        assert other.current.overrides == {"turn.per_user_max": 1}
        # Postgres gone too: the load fails, the last values stay.
        closed = SettingsStore(Database("postgresql://nobody@127.0.0.1:1/none"), other.defaults, dead)
        object.__setattr__(closed, "_current", other.current)
        with pytest.raises(Exception):
            await closed.load()
        assert closed.current.overrides == {"turn.per_user_max": 1}
    finally:
        await other.stop()
        await dead.aclose()


async def test_usage_moves_with_turns_and_answers_within_50_ms_with_20_in_flight(
    client, pooled, upstream, litellm
):
    held = _hung(pooled)
    capacity = pooled.settings.lock_pool_size
    users = [_user(f"use-{i}") for i in range(capacity)]
    idle = (await client.get("/v1/admin/usage", headers=ADMIN)).json()
    assert idle["turns"]["in_flight"] == 0 and idle["turns"]["capacity"] == capacity
    turns = [asyncio.create_task(_turn(held, user)) for user in users]
    await _until(lambda: pooled.turn_limits.in_flight() == capacity)
    timings, busy = [], None
    for _ in range(20):
        started = time.monotonic()
        answer = await client.get("/v1/admin/usage", headers=ADMIN)
        timings.append(time.monotonic() - started)
        busy = answer.json()
    assert busy["turns"]["in_flight"] == capacity, busy
    assert busy["turns"]["top_user_in_flight"] == 1
    assert busy["turns"]["tenants"] == [{"tenant_id": "default", "in_flight": capacity, "limit": None}]
    assert {s["id"] for s in busy["mcp"]} == {s["id"] for s in pooled.broker.mcp_health()}
    assert all(u not in str(busy) for u in (users[0].user_id, "u-use-"))  # counts only
    assert max(timings) < USAGE_BOUND_S, timings
    outcomes = await asyncio.gather(*turns)
    assert {o for o in outcomes} == {("ran", "turn_timeout")}
    after = (await client.get("/v1/admin/usage", headers=ADMIN)).json()
    assert after["turns"]["in_flight"] == 0
    print(
        f"9 usage: {capacity} turns in flight counted, the endpoint answered in "
        f"{max(timings) * 1000:.1f} ms at worst (median {sorted(timings)[10] * 1000:.1f} ms), back to 0 after"
    )


async def test_the_gateway_reads_the_limits_it_enforces(client, services, restored):
    limits = (await client.get("/v1/internal/settings/limits", headers={"X-Internal-Token": "test-internal-token"})).json()
    assert limits["deadline_s"] == 120 and limits["max_concurrent_uploads"] == 2
    await services.platform.save({"docs.uploads_per_minute": 7, "docs.max_concurrent_uploads": 1}, Actor("test"))
    limits = (await client.get("/v1/internal/settings/limits", headers={"X-Internal-Token": "test-internal-token"})).json()
    assert (limits["uploads_per_minute"], limits["max_concurrent_uploads"]) == (7, 1)
    assert services.attachments.in_flight.caps.per_user == 1
    refused = await client.get("/v1/internal/settings/limits", headers={"X-Internal-Token": "nope"})
    assert refused.status_code == 401
