"""Conversation ownership in the agent (ADR-0041, ADR-0050).

The gateway binds each conversation to its first caller; the agent does the same,
independently, before a turn touches the thread. In memory, like the phase 01
checkpointer: phase 02 moves the bindings to Postgres beside the checkpointer.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Owner:
    user_id: str
    tenant_id: str


StoredOwner = Callable[[], Awaitable[Owner | None]]


class ConversationOwners:
    def __init__(self) -> None:
        self._owners: dict[str, Owner] = {}

    async def bind_or_get(self, conversation_id: str, caller: Owner, stored: StoredOwner) -> Owner:
        """The conversation's owner, binding `caller` when it has none. An owner the
        checkpointed thread already records (`stored`) wins over the caller, so a
        registry that lost its bindings cannot hand a thread to someone else."""
        owner = self._owners.get(conversation_id)
        if owner is not None:
            return owner
        recorded = await stored()
        # No await between this check and the insert: concurrent first turns agree on one owner.
        return self._owners.setdefault(conversation_id, recorded or caller)

    async def owner_of(self, conversation_id: str, stored: StoredOwner) -> Owner | None:
        """The owner without binding anyone; None for a conversation nobody has used."""
        return self._owners.get(conversation_id) or await stored()
