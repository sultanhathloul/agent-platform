"""Policy storage (A1, I1, I2) and reload (A2, I3) against the compose Postgres and Redis.

Test policies use scope role:store-test, a policy role no test principal holds, so they never
apply to another test's turns (ADR-0107)."""

import asyncio
import time
import uuid
from pathlib import Path

import pytest
import yaml
from redis.asyncio import Redis

from app.guardrails.checkpoints import Checkpoint
from app.guardrails.schema import GuardEventRecord, Policy
from app.guardrails.store import (
    PolicyExists,
    PolicyFileError,
    PolicyRejected,
    PolicyStore,
    export_document,
    parse_policy_documents,
)

SCOPE = "role:store-test"


def policy(policy_id: str, **overrides) -> Policy:
    fields = {
        "id": policy_id,
        "name_ar": "سياسة اختبار",
        "name_en": "store test",
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


async def test_no_seeded_policy_is_scoped_to_a_tenant(services):
    """ADR-0107: tenants are not policy groups; what the service seeded holds no tenant scope."""
    seeded = [v.policy for v in await services.policies.list_policies() if v.created_by == "seed"]
    assert seeded and all(not p.scope.startswith("tenant:") for p in seeded), [p.scope for p in seeded]


async def test_seeding_inserts_absent_ids_only_and_never_overwrites(store, tmp_path: Path):
    pid = new_id("seed")
    (tmp_path / "a.yaml").write_text(
        f"id: {pid}\nname_ar: سياسة اختبار\nname_en: first\n"
        f"scope: '{SCOPE}'\ncheckpoints: [INPUT]\nchecker: keyword_list\n"
        "config: {keywords: [x]}\naction: warn\n",
        encoding="utf-8",
    )
    assert await store.seed([tmp_path]) == [pid]
    (tmp_path / "a.yaml").write_text(
        f"id: {pid}\nname_ar: سياسة اختبار\nname_en: edited file\n"
        f"scope: '{SCOPE}'\ncheckpoints: [INPUT]\nchecker: keyword_list\n"
        "config: {keywords: [y]}\naction: log_only\nenabled: false\n",
        encoding="utf-8",
    )
    assert await store.seed([tmp_path]) == []
    view = await store.get(pid)
    assert (view.policy.name_en, view.policy.version, view.policy.enabled, view.created_by) == (
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
        policy(pid, name_en="edited", config={"keywords": ["أخرى"]}), actor="admin-2"
    )
    assert second.version == 2
    versions = await store.versions(pid)
    assert [(v.policy.version, v.policy.name_en, v.created_by) for v in versions] == [
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
        "  - {id: ok-one, name_ar: سياسة اختبار, name_en: ok,"
        f" scope: '{SCOPE}', checkpoints: [INPUT], checker: keyword_list,"
        " config: {keywords: [x]}, action: warn}\n"
        "  - {id: bad-one, name_ar: سياسة اختبار, name_en: bad,"
        " scope: 'user:viewer', checkpoints: [INPUT], checker: keyword_list,"
        " config: {keywords: [x]}, action: warn}\n"
        "  - {id: ok-one, name_ar: سياسة اختبار, name_en: dup,"
        f" scope: '{SCOPE}', checkpoints: [INPUT], checker: keyword_list,"
        " config: {keywords: [x]}, action: warn}\n"
    )
    with pytest.raises(PolicyFileError) as raised:
        parse_policy_documents(text)
    errors = {e["id"]: e["error"] for e in raised.value.errors}
    assert "must be global" in errors["bad-one"] and errors["ok-one"] == "appears twice"
    with pytest.raises(PolicyFileError, match="version is assigned"):
        parse_policy_documents(f"{{id: v, version: 3, name_ar: سياسة اختبار, name_en: v, scope: '{SCOPE}'}}")


async def test_export_round_trips_through_import_unchanged(store):
    pid = new_id("export")
    await store.create(policy(pid, description_ar="وصف عربي"), actor="admin")
    document = export_document([(await store.get(pid)).policy])
    assert "وصف عربي" in document and "version" not in document
    report = await store.import_policies(parse_policy_documents(document), actor="importer")
    assert report.unchanged == [pid]


async def test_bilingual_text_and_purpose_round_trip_through_export_and_import(store):
    """ADR-0119: both names, both descriptions and the purpose leave in the export and come back
    from an import unchanged; an import that changes only the Arabic name makes a version."""
    pid = new_id("bilingual")
    made = policy(
        pid,
        name_ar="حظر كلمة الاختبار",
        name_en="Block the test word",
        description_ar="تحظر هذه السياسة كلمة اختبار؛ ومعها رقم 123 ونص English.",
        description_en="Blocks a test word.",
        purpose="test",
    )
    await store.create(made, actor="admin")
    document = export_document([(await store.get(pid)).policy])
    exported = yaml.safe_load(document)["policies"][0]
    texts = ("name_ar", "name_en", "description_ar", "description_en", "purpose")
    assert {k: exported[k] for k in texts} == {
        "name_ar": "حظر كلمة الاختبار",
        "name_en": "Block the test word",
        "description_ar": "تحظر هذه السياسة كلمة اختبار؛ ومعها رقم 123 ونص English.",
        "description_en": "Blocks a test word.",
        "purpose": "test",
    }
    assert "name" not in exported and "description" not in exported
    [parsed] = parse_policy_documents(document)
    assert parsed.content() == made.content()
    assert (await store.import_policies([parsed], actor="importer")).unchanged == [pid]

    renamed = parsed.model_copy(update={"name_ar": "حظر كلمة الاختبار المعدّلة"})
    assert (await store.import_policies([renamed], actor="importer")).versioned == [pid]
    current = (await store.get(pid)).policy
    assert (current.version, current.name_en) == (2, "Block the test word")
    assert current.name_ar == "حظر كلمة الاختبار المعدّلة"


async def test_an_import_without_an_arabic_name_is_refused_whole():
    pid = new_id("noar")
    fields = policy(pid).model_dump(mode="json", exclude={"name_ar", "version"})
    document = yaml.safe_dump({"policies": [fields]}, allow_unicode=True)
    with pytest.raises(PolicyFileError) as raised:
        parse_policy_documents(document)
    assert raised.value.errors == [{"id": pid, "error": "Field required"}]


async def test_every_seeded_policy_has_its_seed_change_row(services):
    """ADR-0119: on a clean database the seed writes the row; on an older one migration 0006 does."""
    seeded = {
        p.id
        for path in (services.settings.config_dir / "policies").glob("*.yaml")
        for p in parse_policy_documents(path.read_text(encoding="utf-8"))
    }
    for pid in sorted(seeded):
        rows = [(c.change, c.version) for c in await services.policies.changes(pid)]
        assert ("seed", 1) in rows, (pid, rows)


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

        async def seen_within(condition, limit_s: float = 2.0) -> float:
            """Seconds until `condition()` holds in the reader; polls past the limit, so the one
            assertion below measures the bound instead of a loop stopping at it."""
            started = time.monotonic()
            while not condition() and time.monotonic() - started < limit_s + 3.0:
                await asyncio.sleep(0.02)
            return time.monotonic() - started

        await writer.create(policy(pid), actor="admin")
        created = await seen_within(lambda: pid in {p.id for p in reader.active()})
        await writer.set_enabled(pid, False, actor="admin")
        switched = await seen_within(lambda: pid not in {p.id for p in reader.active()})
        assert max(created, switched) < 2.0, (created, switched)
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


# ------------------------------------------------------------------ exceptions and the change log (ADR-0108)


async def _change_rows(store: PolicyStore, pid: str) -> list[tuple[str, str, int, bool]]:
    return [(c.change, c.actor, c.version, c.exception) for c in reversed(await store.changes(pid))]


async def test_the_flag_appears_on_an_older_narrow_policy_when_a_wider_one_is_saved_later(store):
    """ADR-0115: the change that turns another policy into an exception names it (`induced`),
    and that policy gets a `flagged` row with the saver as its actor; switching the wider one
    off clears the flag with an `unflagged` row."""
    group = new_id("grp")
    narrow, wide = new_id("narrow"), new_id("wide")
    marker = f"kw{uuid.uuid4().hex[:8]}"  # matches no other test's text, so the global policy is inert
    await store.create(
        policy(narrow, group=group, action="allow", config={"keywords": [marker]}), actor="admin-1"
    )
    assert (await store.get(narrow)).exception is False
    try:
        await store.create(
            policy(wide, group=group, scope="global", action="warn", config={"keywords": [marker]}),
            actor="admin-2",
        )
        older = await store.get(narrow)
        assert older.exception is True and older.overrides == [f"{wide}@1"]
        assert (await store.get(wide)).exception is False
        assert await _change_rows(store, narrow) == [
            ("create", "admin-1", 1, False),
            ("flagged", "admin-2", 1, True),
        ]
        [created] = await store.changes(wide)
        assert (created.change, created.induced) == ("create", [{"policy": f"{narrow}@1", "exception": True}])

        await store.set_enabled(wide, False, actor="admin-3")
        assert (await store.get(narrow)).exception is False  # nothing enabled is weakened any more
        assert (await _change_rows(store, narrow))[-1] == ("unflagged", "admin-3", 1, False)
        disabled = (await store.changes(wide))[0]
        assert (disabled.change, disabled.actor, disabled.induced) == (
            "disable",
            "admin-3",
            [{"policy": f"{narrow}@1", "exception": False}],
        )
    finally:
        await store.set_enabled(wide, False, actor="test")


async def test_import_seed_and_enable_each_write_a_change_row_with_the_actor(store, tmp_path: Path):
    imported, seeded = new_id("imp"), new_id("seed")
    await store.import_policies([policy(imported)], actor="importer")
    await store.import_policies([policy(imported, enabled=False)], actor="importer-2")
    await store.set_enabled(imported, True, actor="admin-x")
    (tmp_path / "s.yaml").write_text(
        f"id: {seeded}\nname_ar: سياسة اختبار\nname_en: seeded\n"
        f"scope: '{SCOPE}'\ncheckpoints: [INPUT]\nchecker: keyword_list\n"
        "config: {keywords: [x]}\naction: warn\n",
        encoding="utf-8",
    )
    await store.seed([tmp_path])
    assert await _change_rows(store, imported) == [
        ("import", "importer", 1, False),
        ("import", "importer-2", 1, False),
        ("enable", "admin-x", 1, False),
    ]
    assert await _change_rows(store, seeded) == [("seed", "seed", 1, False)]


async def test_a_kb_or_tool_policy_that_would_weaken_a_wider_one_at_data_is_refused(store):
    group, wide, weak = new_id("grp"), new_id("wide"), new_id("weak")
    data = {
        "checkpoints": ["DATA"],
        "checker": "regex",
        "config": {"patterns": [{"name": "X", "pattern": "zz9"}]},
    }
    await store.create(policy(wide, group=group, scope=SCOPE, action="redact", **data), actor="admin")
    await store.create(policy(weak, group=group, scope="kb:hr", action="allow", **data), actor="admin")
    try:
        # A global redact over the kb allow would make it an exception at DATA: refused, nothing written.
        with pytest.raises(PolicyRejected) as refused:
            await store.add_version(
                policy(wide, group=group, scope="global", action="redact", **data), actor="admin"
            )
        assert "ADR-0108" in str(refused.value)
        assert (await store.get(wide)).policy.version == 1
        assert [c.change for c in await store.changes(wide)] == ["create"]
    finally:
        await store.set_enabled(weak, False, actor="test")
