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
never leaves a conversation locked.

Release is shielded and runs once: a cancellation that lands while it runs cannot
stop it halfway, and every connection the pool takes back is reset with
pg_advisory_unlock_all(), so no pooled connection ever holds a conversation's lock
(review round 1, ADR-0109). The unlock is bounded (RELEASE_TIMEOUT_S): past it the connection is
closed, which ends the session and with it the lock, and the pool discards it (the PR #12 review,
decision 7, ADR-0267). When every lock connection is in use for
`getconn_timeout_s`, `acquire` raises LockPoolBusy, which the route answers with
503 `service_busy`."""

import asyncio
import logging
import time
import uuid

from psycopg_pool import AsyncConnectionPool, PoolTimeout

log = logging.getLogger(__name__)
# The bound on a lease's unlock; past it the connection is closed and discarded.
RELEASE_TIMEOUT_S = 5.0


def lock_key(conversation_id: str) -> int:
    """The conversation UUID's upper 64 bits as a signed bigint."""
    value = uuid.UUID(conversation_id).int >> 64
    return value - (1 << 64) if value >= 1 << 63 else value


class LockPoolBusy(RuntimeError):
    """Every lock connection stayed in use for the pool timeout (503 `service_busy`)."""


async def _reset(conn) -> None:
    """Runs on every connection the pool takes back: no session lock survives a return."""
    await conn.execute("SELECT pg_advisory_unlock_all()")


class TurnLease:
    """A held lock. `release` may be called from several places (the turn task, the
    response's background task) and any number of times: the unlock and the return of
    the connection run once, in a task of their own that a cancellation cannot stop."""

    def __init__(self, pool: AsyncConnectionPool, conn, key: int, conversation_id: str):
        self._pool = pool
        self._conn = conn
        self._key = key
        self.conversation_id = conversation_id
        self._releasing: asyncio.Future | None = None

    @property
    def released(self) -> bool:
        return self._releasing is not None and self._releasing.done()

    async def release(self) -> None:
        if self._releasing is None:
            self._releasing = asyncio.ensure_future(self._release())
        await asyncio.shield(self._releasing)

    async def _release(self) -> None:
        try:
            # asyncio.timeout, not a cancel scope: the cancellation is delivered once, so closing
            # the connection and returning it below are not cancelled in turn (ADR-0267).
            async with asyncio.timeout(RELEASE_TIMEOUT_S):
                await self._conn.execute("SELECT pg_advisory_unlock(%s)", (self._key,))
        except TimeoutError:
            log.warning(
                "turn lock unlock for %s past %.0f s: connection closed",
                self.conversation_id,
                RELEASE_TIMEOUT_S,
            )
            await self._conn.close()  # the session ends, and with it the lock; the pool discards it
        except Exception as exc:  # the pool's reset, or the session's end, releases it anyway
            log.warning("turn lock unlock failed for %s: %s", self.conversation_id, type(exc).__name__)
        finally:
            await self._pool.putconn(self._conn)


class TurnLocks:
    def __init__(self, conninfo: str, *, size: int, getconn_timeout_s: float = 5.0):
        self._timeout = getconn_timeout_s
        self.pool = AsyncConnectionPool(
            conninfo,
            open=False,
            min_size=0,
            max_size=size,
            kwargs={"autocommit": True, "prepare_threshold": 0},
            reset=_reset,
        )

    async def open(self) -> None:
        await self.pool.open(wait=False)

    async def close(self) -> None:
        await self.pool.close()

    async def acquire(self, conversation_id: str, *, wait_s: float = 0.0) -> TurnLease | None:
        """The lease, or None when another turn holds the conversation for longer than `wait_s`.
        Raises LockPoolBusy when no lock connection frees up within the pool timeout."""
        key = lock_key(conversation_id)
        deadline = time.monotonic() + wait_s
        try:
            conn = await self.pool.getconn(timeout=self._timeout)
        except PoolTimeout as exc:
            raise LockPoolBusy(f"no lock connection within {self._timeout} s") from exc
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
