"""Conversation ownership in the agent (ADR-0041, ADR-0050).

The gateway binds each conversation to its first caller; the agent does the same,
independently, before a turn touches the thread. The bindings live in Postgres
(`agent.conversation_owners`) beside the checkpointer, so a restart keeps both
(ADR-0055).
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from app.db import Database


@dataclass(frozen=True)
class Owner:
    user_id: str
    tenant_id: str


StoredOwner = Callable[[], Awaitable[Owner | None]]

# The longest title kept for a conversation (ADR-0192).
TITLE_MAX_CHARS = 80


@dataclass(frozen=True)
class ConversationRow:
    conversation_id: str
    title: str
    last_turn_at: datetime


def title_of(question: str) -> str:
    """A conversation's title: its first question on one line, at most TITLE_MAX_CHARS."""
    line = " ".join(question.split())
    return line if len(line) <= TITLE_MAX_CHARS else line[: TITLE_MAX_CHARS - 1].rstrip() + "…"


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

    async def record_turn(self, conversation_id: str, question: str) -> None:
        """After a turn ends with `done`: the conversation's latest turn time, and its title
        once, from the first question it was given (ADR-0192)."""
        async with self._db.connection() as conn:
            await conn.execute(
                "UPDATE agent.conversation_owners SET last_turn_at = now(), "
                "title = CASE WHEN title IS NULL OR title = '' THEN %s ELSE title END "
                "WHERE conversation_id = %s",
                (title_of(question), conversation_id),
            )

    async def untitled(self, owner: Owner, limit: int) -> list[str]:
        """The owner's conversations recorded before titles existed, not read yet."""
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT conversation_id::text AS id FROM agent.conversation_owners "
                "WHERE tenant_id = %s AND user_id = %s AND title IS NULL "
                "ORDER BY created_at DESC LIMIT %s",
                (owner.tenant_id, owner.user_id, limit),
            )
            return [row["id"] for row in await cursor.fetchall()]

    async def fill(self, conversation_id: str, title: str, last_turn_at: datetime | None) -> None:
        """Fills a row read from its thread; an empty title marks a thread without an exchange."""
        async with self._db.connection() as conn:
            await conn.execute(
                "UPDATE agent.conversation_owners SET title = %s, "
                "last_turn_at = COALESCE(last_turn_at, %s) WHERE conversation_id = %s AND title IS NULL",
                (title, last_turn_at, conversation_id),
            )

    async def listed(self, owner: Owner, limit: int, before: datetime | None) -> list[ConversationRow]:
        """The owner's conversations with a finished turn, newest first, older than `before`."""
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT conversation_id::text AS id, COALESCE(title, '') AS title, last_turn_at "
                "FROM agent.conversation_owners "
                "WHERE tenant_id = %s AND user_id = %s AND last_turn_at IS NOT NULL "
                "AND (%s::timestamptz IS NULL OR last_turn_at < %s::timestamptz) "
                "ORDER BY last_turn_at DESC, conversation_id DESC LIMIT %s",
                (owner.tenant_id, owner.user_id, before, before, limit),
            )
            return [ConversationRow(r["id"], r["title"], r["last_turn_at"]) for r in await cursor.fetchall()]
