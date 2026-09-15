"""The agent's Postgres database (README §2 state store, §8 memory store; ADR-0055).

One connection pool to the `app` database serves the LangGraph checkpointer and
the agent's own tables, all in schema `agent`: conversation owners, memory items
and settings, and recorded context packages. The migrations under
app/db/migrations/ are applied once each, in file-name order, when the service
starts; the checkpointer applies its own migrations beside them.

The pool is created closed, so building the services never connects; the app's
lifespan (or a test fixture) opens it."""

import logging
from pathlib import Path

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

log = logging.getLogger(__name__)

SCHEMA = "agent"
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
# Serializes migrations when several agent processes start at once.
_MIGRATION_LOCK = 720_055


def _statements(sql: str) -> list[str]:
    """One migration file as single statements: a prepared statement takes one
    command. Migrations end each statement with `;` at the end of a line and use
    no dollar-quoted bodies."""
    lines = [line for line in sql.splitlines() if not line.lstrip().startswith("--")]
    return [part.strip() for part in "\n".join(lines).split(";\n") if part.strip().rstrip(";").strip()]


class Database:
    def __init__(self, conninfo: str, *, max_size: int = 10):
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
                "options": f"-c search_path={SCHEMA},public",
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
            await conn.execute("SELECT pg_advisory_lock(%s)", (_MIGRATION_LOCK,))
            try:
                await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
                await conn.execute(
                    f"CREATE TABLE IF NOT EXISTS {SCHEMA}.schema_migrations ("
                    "version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
                )
                rows = await (await conn.execute(f"SELECT version FROM {SCHEMA}.schema_migrations")).fetchall()
                applied = {row["version"] for row in rows}
                for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                    if path.stem in applied:
                        continue
                    async with conn.transaction():
                        for statement in _statements(path.read_text(encoding="utf-8")):
                            await conn.execute(statement)
                        await conn.execute(
                            f"INSERT INTO {SCHEMA}.schema_migrations (version) VALUES (%s)", (path.stem,)
                        )
                    log.info("applied migration %s", path.name)
            finally:
                await conn.execute("SELECT pg_advisory_unlock(%s)", (_MIGRATION_LOCK,))
