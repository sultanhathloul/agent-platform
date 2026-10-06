"""One turn at a time per conversation (D6).

Two turns in flight on one thread would each start from the checkpoint before the
other, and the last writer would win: an exchange, or the end-of-turn fold's
summary and cursor, would be lost; a retract racing a turn could lose its flag
while its memory delete stood. `TurnRunner` therefore holds a Postgres session
advisory lock keyed on the conversation id around the graph invocation and
around `retract` (phase 08's resume will take the same lock).

The lock lives on a dedicated connection pool of LOCK_POOL_SIZE connections, never
the checkpointer's pool: a held lock pins its connection for the whole turn. A
turn that finds the lock held fails at once with `turn_in_flight` (HTTP 409 at
the gateway); a retract waits a few seconds for the turn it follows to finish.
Postgres releases a session lock when its connection ends, so a crashed process
never leaves a conversation locked."""

import asyncio
import logging
import time
import uuid

from psycopg_pool import AsyncConnectionPool

log = logging.getLogger(__name__)


def lock_key(conversation_id: str) -> int:
    """The conversation UUID's upper 64 bits as a signed bigint."""
    value = uuid.UUID(conversation_id).int >> 64
    return value - (1 << 64) if value >= 1 << 63 else value


class TurnLease:
    """A held lock. `release` is idempotent and may be called from several places
    (the stream's end, the response's background task)."""

    def __init__(self, pool: AsyncConnectionPool, conn, key: int, conversation_id: str):
        self._pool = pool
        self._conn = conn
        self._key = key
        self.conversation_id = conversation_id
        self.released = False

    async def release(self) -> None:
        if self.released:
            return
        self.released = True
        try:
            await self._conn.execute("SELECT pg_advisory_unlock(%s)", (self._key,))
        except Exception as exc:  # the session ending releases the lock anyway
            log.warning("turn lock unlock failed for %s: %s", self.conversation_id, type(exc).__name__)
        finally:
            await self._pool.putconn(self._conn)


class TurnLocks:
    def __init__(self, conninfo: str, *, size: int, getconn_timeout_s: float = 30.0):
        self._timeout = getconn_timeout_s
        self.pool = AsyncConnectionPool(
            conninfo,
            open=False,
            min_size=0,
            max_size=size,
            kwargs={"autocommit": True, "prepare_threshold": 0},
        )

    async def open(self) -> None:
        await self.pool.open(wait=False)

    async def close(self) -> None:
        await self.pool.close()

    async def acquire(self, conversation_id: str, *, wait_s: float = 0.0) -> TurnLease | None:
        """The lease, or None when another turn holds the conversation for longer than `wait_s`."""
        key = lock_key(conversation_id)
        deadline = time.monotonic() + wait_s
        conn = await self.pool.getconn(timeout=self._timeout)
        try:
            while True:
                row = await (await conn.execute("SELECT pg_try_advisory_lock(%s)", (key,))).fetchone()
                if row[0]:
                    return TurnLease(self.pool, conn, key, conversation_id)
                if time.monotonic() >= deadline:
                    await self.pool.putconn(conn)
                    return None
                await asyncio.sleep(0.05)
        except BaseException:
            await self.pool.putconn(conn)
            raise

    async def held(self, conversation_id: str) -> bool:
        """Whether some session holds the conversation's lock (tests, diagnostics)."""
        lease = await self.acquire(conversation_id)
        if lease is None:
            return True
        await lease.release()
        return False
