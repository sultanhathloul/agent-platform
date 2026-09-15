"""Conversation ownership in the agent (ADR-0041, ADR-0050).

The gateway binds each conversation to its first caller; the agent does the same,
independently, before a turn touches the thread. The bindings live in Postgres
(`agent.conversation_owners`) beside the checkpointer, so a restart keeps both
(ADR-0055).
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.db import Database


@dataclass(frozen=True)
class Owner:
    user_id: str
    tenant_id: str


StoredOwner = Callable[[], Awaitable[Owner | None]]


class ConversationOwners:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def _bound(self, conversation_id: str) -> Owner | None:
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT user_id, tenant_id FROM agent.conversation_owners WHERE conversation_id = %s",
                (conversation_id,),
            )
            row = await cursor.fetchone()
        return Owner(row["user_id"], row["tenant_id"]) if row else None

    async def bind_or_get(self, conversation_id: str, caller: Owner, stored: StoredOwner) -> Owner:
        """The conversation's owner, binding `caller` when it has none. An owner the
        checkpointed thread already records (`stored`) wins over the caller, so a
        registry that lost its bindings cannot hand a thread to someone else."""
        owner = await self._bound(conversation_id)
        if owner is not None:
            return owner
        candidate = await stored() or caller
        async with self._db.connection() as conn:
            await conn.execute(
                "INSERT INTO agent.conversation_owners (conversation_id, user_id, tenant_id) "
                "VALUES (%s, %s, %s) ON CONFLICT (conversation_id) DO NOTHING",
                (conversation_id, candidate.user_id, candidate.tenant_id),
            )
        # Concurrent first turns: one insert wins, and every caller reads that row back.
        bound = await self._bound(conversation_id)
        assert bound is not None
        return bound

    async def owner_of(self, conversation_id: str, stored: StoredOwner) -> Owner | None:
        """The owner without binding anyone; None for a conversation nobody has used."""
        return await self._bound(conversation_id) or await stored()
