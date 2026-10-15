"""The agent's Postgres database (README §2 state store, §8 memory store; ADR-0055).

One connection pool to the `app` database serves the LangGraph checkpointer and
the agent's own tables, all in schema `agent`: conversation owners, memory items
and settings, and recorded context packages. The migrations under
app/db/migrations/ are applied once each, in file-name order, when the service
starts; the checkpointer applies its own migrations beside them. Each applied
file's SHA-256 is recorded, and startup fails when a file no longer matches
what was applied (ADR-0069): a migration is never edited, a new one is added.

The pool is created closed, so building the services never connects; the app's
lifespan (or a test fixture) opens it."""

import hashlib
import logging
import re
import zlib
from pathlib import Path

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

log = logging.getLogger(__name__)

SCHEMA = "agent"
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
# Serializes migrations when several agent processes start at once.
_MIGRATION_LOCK = 720_055


class MigrationChecksumMismatch(RuntimeError):
    pass


def _checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def _statements(sql: str) -> list[str]:
    """One migration file as single statements: a prepared statement takes one
    command. Migrations end each statement with `;` at the end of a line and use
    no dollar-quoted bodies."""
    lines = [line for line in sql.splitlines() if not line.lstrip().startswith("--")]
    return [part.strip() for part in "\n".join(lines).split(";\n") if part.strip().rstrip(";").strip()]


class Database:
    def __init__(
        self,
        conninfo: str,
        *,
        max_size: int = 10,
        migrations_dir: Path = MIGRATIONS_DIR,
        schema: str = SCHEMA,
    ):
        """`schema`: where the migrations are recorded and the search path starts; the agent's
        `agent`, or another service's own (ADR-0211)."""
        if not re.match(r"^[a-z_][a-z0-9_]{0,62}$", schema):
            raise ValueError(f"invalid schema name {schema!r}")
        self._migrations_dir = migrations_dir
        self.schema = schema
        self._lock = (
            _MIGRATION_LOCK if schema == SCHEMA else _MIGRATION_LOCK + zlib.crc32(schema.encode()) % 100_000
        )
        self.pool = AsyncConnectionPool(
            conninfo,
            open=False,
            min_size=1,
            max_size=max_size,
            # The checkpointer needs autocommit and dict rows; `vector` lives in public.
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
                "options": f"-c search_path={schema},public",
            },
        )

    async def open(self, *, timeout: float = 60.0) -> None:
        await self.pool.open(wait=True, timeout=timeout)
        await self._migrate()

    async def close(self) -> None:
        await self.pool.close()

    def connection(self):
        return self.pool.connection()

    async def _migrate(self) -> None:
        async with self.pool.connection() as conn:
            await conn.execute("SELECT pg_advisory_lock(%s)", (self._lock,))
            try:
                # Created only when missing: a role that owns its schema but may not create one in the
                # database (a reference server's, ADR-0211) still starts.
                exists = await (
                    await conn.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (self.schema,))
                ).fetchone()
                if exists is None:
                    await conn.execute(f"CREATE SCHEMA {self.schema}")
                await conn.execute(
                    f"CREATE TABLE IF NOT EXISTS {self.schema}.schema_migrations ("
                    "version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
                )
                # Databases migrated before checksums existed gain the column (ADR-0069).
                await conn.execute(
                    f"ALTER TABLE {self.schema}.schema_migrations ADD COLUMN IF NOT EXISTS checksum text"
                )
                rows = await (
                    await conn.execute(f"SELECT version, checksum FROM {self.schema}.schema_migrations")
                ).fetchall()
                applied = {row["version"]: row["checksum"] for row in rows}
                for path in sorted(self._migrations_dir.glob("*.sql")):
                    sql = path.read_text(encoding="utf-8")
                    checksum = _checksum(sql)
                    if path.stem in applied:
                        recorded = applied[path.stem]
                        if recorded is None:
                            # Applied before checksums were recorded: the file as it is now is the baseline.
                            await conn.execute(
                                f"UPDATE {self.schema}.schema_migrations SET checksum = %s "
                                "WHERE version = %s",
                                (checksum, path.stem),
                            )
                        elif recorded != checksum:
                            raise MigrationChecksumMismatch(
                                f"migration {path.name} changed after it was applied "
                                f"(recorded {recorded[:12]}, file {checksum[:12]}); "
                                "add a new migration instead"
                            )
                        continue
                    async with conn.transaction():
                        for statement in _statements(sql):
                            await conn.execute(statement)
                        await conn.execute(
                            f"INSERT INTO {self.schema}.schema_migrations (version, checksum) "
                            "VALUES (%s, %s)",
                            (path.stem, checksum),
                        )
                    log.info("applied migration %s", path.name)
            finally:
                await conn.execute("SELECT pg_advisory_unlock(%s)", (self._lock,))
