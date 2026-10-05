"""The agent's migration runner records a checksum per file and refuses to start when
an applied file has changed (ADR-0069)."""

import uuid

import pytest

from app.db import Database, MigrationChecksumMismatch

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
