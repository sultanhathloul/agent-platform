"""The agent's migration runner records a checksum per file and refuses to start when
an applied file has changed (ADR-0069)."""

import uuid

import pytest
from psycopg.types.json import Jsonb

from app.db import MIGRATIONS_DIR, Database, MigrationChecksumMismatch, _statements
from app.guardrails.schema import Policy

pytestmark = pytest.mark.integration


async def test_a_changed_migration_fails_startup(database_url, tmp_path):
    version = f"9000_probe_{uuid.uuid4().hex[:8]}"
    table = f"agent.probe_{version[-8:]}"
    migration = tmp_path / f"{version}.sql"
    migration.write_text(f"CREATE TABLE {table} (id int);\n", encoding="utf-8")

    db = Database(database_url, max_size=1, migrations_dir=tmp_path)
    try:
        await db.open()
        async with db.connection() as conn:
            row = await (
                await conn.execute(
                    "SELECT checksum FROM agent.schema_migrations WHERE version = %s", (version,)
                )
            ).fetchone()
        assert row["checksum"] and len(row["checksum"]) == 64
        await db._migrate()  # unchanged: nothing to do, no error

        migration.write_text(f"CREATE TABLE {table} (id bigint);\n", encoding="utf-8")
        with pytest.raises(MigrationChecksumMismatch, match=version):
            await db._migrate()

        # A row from before checksums existed takes the file as it is now as its baseline.
        async with db.connection() as conn:
            await conn.execute(
                "UPDATE agent.schema_migrations SET checksum = NULL WHERE version = %s", (version,)
            )
        await db._migrate()
        await db._migrate()
    finally:
        async with db.connection() as conn:
            await conn.execute("DELETE FROM agent.schema_migrations WHERE version = %s", (version,))
            await conn.execute(f"DROP TABLE IF EXISTS {table}")
        await db.close()


async def _run(conn, name: str) -> None:
    for statement in _statements((MIGRATIONS_DIR / name).read_text(encoding="utf-8")):
        await conn.execute(statement)


async def _old_version(conn, pid: str, content: dict) -> None:
    """A version as stored before migrations 0004 and 0007: no change row, a single-language name."""
    await conn.execute(
        "INSERT INTO agent.guard_policies (id, version, content, created_by) VALUES (%s, 1, %s, 'seed')",
        (pid, Jsonb(content)),
    )
    await conn.execute(
        "INSERT INTO agent.guard_policy_heads (id, current_version, enabled, updated_by) "
        "VALUES (%s, 1, false, 'seed')",
        (pid,),
    )


OLD_CONTENT = {
    "name": "Old policy",
    "description": "Saved before the bilingual fields.",
    "scope": "role:migration-test",
    "checkpoints": ["INPUT"],
    "checker": "keyword_list",
    "config": {"keywords": ["x"]},
    "action": "warn",
}


async def test_0006_backfills_one_seed_row_per_version_without_a_change_row(services):
    """ADR-0119: each version without a change row gets `seed` by `backfill`; a second run adds
    nothing, and a version that has a row keeps it alone. Rolled back: the test leaves no rows."""
    pid = f"mig-{uuid.uuid4().hex[:10]}"
    async with services.db.connection() as conn, conn.transaction(force_rollback=True):
        await _old_version(conn, pid, OLD_CONTENT)
        await _run(conn, "0006_backfill_policy_changes.sql")
        await _run(conn, "0006_backfill_policy_changes.sql")
        rows = await (
            await conn.execute(
                "SELECT c.version, change, actor, c.exception, c.overrides, induced, "
                "changed_at = p.created_at AS dated FROM agent.guard_policy_changes c "
                "JOIN agent.guard_policies p ON p.id = c.policy_id AND p.version = c.version "
                "WHERE c.policy_id = %s",
                (pid,),
            )
        ).fetchall()
        assert [dict(r) for r in rows] == [
            {
                "version": 1,
                "change": "seed",
                "actor": "backfill",
                "exception": False,
                "overrides": [],
                "induced": [],
                "dated": True,
            }
        ]
        orphans = await (
            await conn.execute(
                "SELECT count(*) AS n FROM agent.guard_policies p WHERE NOT EXISTS (SELECT 1 FROM "
                "agent.guard_policy_changes c WHERE c.policy_id = p.id AND c.version = p.version)"
            )
        ).fetchone()
        assert orphans["n"] == 0


async def test_0007_moves_name_and_description_to_the_english_fields(services):
    """ADR-0119: `name` and `description` become `name_en` and `description_en`; `name_ar` starts
    as the English name and `description_ar` empty, so the stored version validates; a second run
    changes nothing. Rolled back."""
    pid = f"mig-{uuid.uuid4().hex[:10]}"
    async with services.db.connection() as conn, conn.transaction(force_rollback=True):
        await _old_version(conn, pid, OLD_CONTENT)
        await _run(conn, "0007_bilingual_policy_text.sql")
        await _run(conn, "0007_bilingual_policy_text.sql")
        row = await (
            await conn.execute("SELECT content FROM agent.guard_policies WHERE id = %s", (pid,))
        ).fetchone()
        content = row["content"]
        assert "name" not in content and "description" not in content
        assert (content["name_en"], content["name_ar"]) == ("Old policy", "Old policy")
        assert content["description_en"] == "Saved before the bilingual fields."
        assert content["description_ar"] == ""
        policy = Policy.model_validate({**content, "id": pid, "version": 1})
        assert policy.purpose == "production"
