"""The main graph's checkpointer: Postgres (ADR-0055), each write bounded (the PR #12 review,
decision 2, ADR-0263).

A turn's exchange is persisted by the checkpoint written after persist_state. Every checkpoint
write of a turn takes at most min(config/turn.yaml `persist_s`, what remains of the turn's
deadline); the guards on the whole answer end `persist_s` before the deadline, so the exchange's
write always has its full bound. asyncio.timeout, not a cancel scope: the cancellation is delivered
once, so the pool takes the connection back (and discards it, left mid-query) uncancelled."""

import asyncio

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.resilience import deadline


class BoundedSaver(AsyncPostgresSaver):
    def __init__(self, conn, *, persist_s: float, **kwargs):
        super().__init__(conn, **kwargs)
        self.persist_s = persist_s

    def _bound(self) -> float:
        return max(deadline.bounded(self.persist_s), 0.0)

    async def aput(self, *args, **kwargs):
        async with asyncio.timeout(self._bound()):
            return await super().aput(*args, **kwargs)

    async def aput_writes(self, *args, **kwargs):
        async with asyncio.timeout(self._bound()):
            return await super().aput_writes(*args, **kwargs)
