"""Policy storage (A1, I1, I2) and reload (A2, I3) against the compose Postgres and Redis.

Test policies use scope role:store-test, a policy role no test principal holds, so they never
apply to another test's turns (ADR-0107)."""

import asyncio
import time
import uuid
from pathlib import Path

import pytest
from redis.asyncio import Redis

from app.guardrails.checkpoints import Checkpoint
from app.guardrails.schema import GuardEventRecord, Policy
from app.guardrails.store import (
    PolicyExists,
    PolicyFileError,
    PolicyStore,
    export_document,
    parse_policy_documents,
)

SCOPE = "role:store-test"


def policy(policy_id: str, **overrides) -> Policy:
    fields = {
        "id": policy_id,
        "name": "store test",
        "scope": SCOPE,
        "checkpoints": ["INPUT"],
        "checker": "keyword_list",
        "config": {"keywords": ["كلمة"]},
        "action": "warn",
    }
    return Policy.model_validate({**fields, **overrides})


def new_id(prefix: str = "store") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


@pytest.fixture
async def redis(settings):
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
async def store(services, redis, settings):
    store = PolicyStore(services.db, redis, namespace=settings.redis_namespace)
    await store.load()
    return store


async def test_the_service_seeded_every_policy_file_once(services):
    ids = {view.policy.id for view in await services.policies.list_policies()}
    files = {
        p.id
        for path in (services.settings.config_dir / "policies").glob("*.yaml")
        for p in parse_policy_documents(path.read_text(encoding="utf-8"))
    }
    assert files <= ids
    assert "topics-politics-llm-judge" in files
    active = {p.id for p in services.policies.active()}
    assert "topics-politics-llm-judge" not in active and "pii-redact-output" in active


async def test_seeding_inserts_absent_ids_only_and_never_overwrites(store, tmp_path: Path):
    pid = new_id("seed")
    (tmp_path / "a.yaml").write_text(
        f"id: {pid}\nname: first\nscope: '{SCOPE}'\ncheckpoints: [INPUT]\nchecker: keyword_list\n"
        "config: {keywords: [x]}\naction: warn\n",
        encoding="utf-8",
    )
    assert await store.seed([tmp_path]) == [pid]
    (tmp_path / "a.yaml").write_text(
        f"id: {pid}\nname: edited file\nscope: '{SCOPE}'\ncheckpoints: [INPUT]\nchecker: keyword_list\n"
        "config: {keywords: [y]}\naction: log_only\nenabled: false\n",
        encoding="utf-8",
    )
    assert await store.seed([tmp_path]) == []
    view = await store.get(pid)
    assert (view.policy.name, view.policy.version, view.policy.enabled, view.created_by) == (
        "first",
        1,
        True,
        "seed",
    )


async def test_an_edit_is_a_new_immutable_version_and_the_switch_is_not(store):
    pid = new_id()
    first = await store.create(policy(pid), actor="admin-1")
    assert first.version == 1
    second = await store.add_version(
        policy(pid, name="edited", config={"keywords": ["أخرى"]}), actor="admin-2"
    )
    assert second.version == 2
    versions = await store.versions(pid)
    assert [(v.policy.version, v.policy.name, v.created_by) for v in versions] == [
        (2, "edited", "admin-2"),
        (1, "store test", "admin-1"),
    ]
    assert versions[1].policy.config == {"keywords": ["كلمة"]}  # version 1 untouched

    switched = await store.set_enabled(pid, False, actor="admin-3")
    assert (switched.version, switched.enabled) == (2, False)
    assert len(await store.versions(pid)) == 2
    view = await store.get(pid)
    assert (view.updated_by, view.policy.enabled) == ("admin-3", False)
    assert pid not in {p.id for p in store.active()}

    with pytest.raises(PolicyExists):
        await store.create(policy(pid), actor="admin-1")


async def test_import_creates_versions_switches_or_leaves_alone(store):
    kept, edited, switched, fresh = new_id(), new_id(), new_id(), new_id()
    for pid in (kept, edited, switched):
        await store.create(policy(pid), actor="admin")
    report = await store.import_policies(
        [
            policy(kept),
            policy(edited, action="log_only"),
            policy(switched, enabled=False),
            policy(fresh),
        ],
        actor="importer",
    )
    assert (report.unchanged, report.versioned, report.switched, report.created) == (
        [kept],
        [edited],
        [switched],
        [fresh],
    )
    assert (await store.get(edited)).policy.version == 2
    assert (await store.get(switched)).policy.version == 1


async def test_an_invalid_document_is_rejected_whole():
    text = (
        "policies:\n"
        f"  - {{id: ok-one, name: ok, scope: '{SCOPE}', checkpoints: [INPUT], checker: keyword_list,"
        " config: {keywords: [x]}, action: warn}\n"
        f"  - {{id: bad-one, name: bad, scope: 'user:viewer', checkpoints: [INPUT], checker: keyword_list,"
        " config: {keywords: [x]}, action: warn}\n"
        f"  - {{id: ok-one, name: dup, scope: '{SCOPE}', checkpoints: [INPUT], checker: keyword_list,"
        " config: {keywords: [x]}, action: warn}\n"
    )
    with pytest.raises(PolicyFileError) as raised:
        parse_policy_documents(text)
    errors = {e["id"]: e["error"] for e in raised.value.errors}
    assert "must be global" in errors["bad-one"] and errors["ok-one"] == "appears twice"
    with pytest.raises(PolicyFileError, match="version is assigned"):
        parse_policy_documents(f"{{id: v, version: 3, name: v, scope: '{SCOPE}'}}")


async def test_export_round_trips_through_import_unchanged(store):
    pid = new_id("export")
    await store.create(policy(pid, description="وصف عربي"), actor="admin")
    document = export_document([(await store.get(pid)).policy])
    assert "وصف عربي" in document and "version" not in document
    report = await store.import_policies(parse_policy_documents(document), actor="importer")
    assert report.unchanged == [pid]


async def test_a_change_reaches_another_process_within_two_seconds(services, settings):
    """I3: two stores with their own Redis connections stand for two agent processes."""
    clients = [Redis.from_url(settings.redis_url, decode_responses=True) for _ in range(2)]
    writer = PolicyStore(services.db, clients[0], namespace=settings.redis_namespace, refresh_s=3600)
    reader = PolicyStore(services.db, clients[1], namespace=settings.redis_namespace, refresh_s=3600)
    await writer.start()
    await reader.start()
    try:
        await asyncio.sleep(0.2)  # the reader's subscription is up
        pid = new_id("reload")
        started = time.monotonic()
        await writer.create(policy(pid), actor="admin")
        while pid not in {p.id for p in reader.active()}:
            assert time.monotonic() - started < 2.0, "the reader did not see the new policy within 2 s"
            await asyncio.sleep(0.02)
        created_after = time.monotonic() - started

        started = time.monotonic()
        await writer.set_enabled(pid, False, actor="admin")
        while pid in {p.id for p in reader.active()}:
            assert time.monotonic() - started < 2.0, "the reader did not see the switch within 2 s"
            await asyncio.sleep(0.02)
        assert created_after < 2.0
    finally:
        await writer.stop()
        await reader.stop()
        for client in clients:
            await client.aclose()


async def test_matched_events_are_counted_per_day_without_content(store):
    pid = new_id("metrics")
    events = [
        GuardEventRecord(
            checkpoint=Checkpoint.DATA, policy_id=pid, policy_version=1, action="redact", target="answer"
        ),
        GuardEventRecord(
            checkpoint=Checkpoint.DATA, policy_id=pid, policy_version=1, action="redact", target="answer"
        ),
        GuardEventRecord(checkpoint=Checkpoint.INPUT, action="allow", detail="no policy"),
    ]
    stored = await store.record_events(
        events, tenant_id="t", conversation_id=str(uuid.uuid4()), turn_id="x" * 32
    )
    assert stored == 2
    rows = [r for r in await store.matches_per_day(7) if r["policy_id"] == pid]
    assert [(r["action"], r["matches"]) for r in rows] == [("redact", 2)]
