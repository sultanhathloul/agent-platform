"""Bounded checkpoint storage per conversation (report library PR, owner decision 7, ADR-0437).

LangGraph's Postgres checkpointer keeps every checkpoint of a thread (about 18 a turn) and, for the
`messages` channel, a blob of the whole history at each new version (one a turn). Nothing reads them but the
latest: a conversation's history, its summary and the next turn all start from the newest checkpoint. So a
conversation's stored size grew with the square of its length. After each turn, while the conversation's
turn lock is still held (so no other write of this thread is in flight), the thread keeps its newest
`keep` checkpoints and deletes the older ones, their pending writes and every blob no remaining checkpoint
names. One transaction, bounded by `prune_s` (rule 12c); the latest checkpoint is never touched."""

import asyncio
import logging

log = logging.getLogger(__name__)

_OLD = (
    "SELECT checkpoint_id FROM agent.checkpoints WHERE thread_id = %(thread)s AND checkpoint_ns = '' "
    "AND checkpoint_id < (SELECT min(checkpoint_id) FROM (SELECT checkpoint_id FROM agent.checkpoints "
    "WHERE thread_id = %(thread)s AND checkpoint_ns = '' ORDER BY checkpoint_id DESC LIMIT %(keep)s) kept)"
)


async def prune_thread(db, thread_id: str, *, keep: int, timeout_s: float) -> int:
    """Deletes the thread's checkpoints older than its newest `keep`; returns how many."""
    params = {"thread": thread_id, "keep": keep}
    async with asyncio.timeout(timeout_s):
        async with db.connection() as conn, conn.transaction():
            await conn.execute(
                f"DELETE FROM agent.checkpoint_writes WHERE thread_id = %(thread)s AND checkpoint_ns = '' "
                f"AND checkpoint_id IN ({_OLD})", params)
            cursor = await conn.execute(
                f"DELETE FROM agent.checkpoints WHERE thread_id = %(thread)s AND checkpoint_ns = '' "
                f"AND checkpoint_id IN ({_OLD})", params)
            deleted = cursor.rowcount or 0
            if deleted:
                await conn.execute(
                    "DELETE FROM agent.checkpoint_blobs b WHERE b.thread_id = %(thread)s AND b.checkpoint_ns = '' "
                    "AND NOT EXISTS (SELECT 1 FROM agent.checkpoints c WHERE c.thread_id = b.thread_id "
                    "AND c.checkpoint_ns = b.checkpoint_ns AND c.checkpoint -> 'channel_versions' ->> b.channel = b.version)",
                    params)
    return deleted
