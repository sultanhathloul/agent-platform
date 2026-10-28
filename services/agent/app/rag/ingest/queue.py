"""The ingestion queue (ADR-0130): attachment ids on a Redis list, and KB document ids as
`kb:<id>` (phase 08). Registering an upload puts its id here; the ingest service takes it (app/rag/ingest/service.py). The agent only enqueues:
it never parses a file (owner decision R4)."""

from collections.abc import Sequence


def queue_name(namespace: str) -> str:
    return f"{namespace}:rag:ingest"


# A KB document's queue item (phase 08, I2): its id behind this prefix; an attachment's is its id.
KB_ITEM = "kb:"


def kb_item(document_id: str) -> str:
    return f"{KB_ITEM}{document_id}"


class IngestQueue:
    def __init__(self, redis, *, namespace: str):
        self._redis = redis
        self.name = queue_name(namespace)

    async def enqueue(self, attachment_id: str) -> None:
        if self._redis is None:
            raise RuntimeError("the ingestion queue needs Redis (REDIS_URL)")
        await self._redis.rpush(self.name, attachment_id)

    async def requeue(self, attachment_ids: Sequence[str]) -> None:
        """The ingest service at start: every pending attachment again, once."""
        await self._redis.delete(self.name)
        for attachment_id in attachment_ids:
            await self._redis.rpush(self.name, attachment_id)

    async def take(self, timeout_s: float) -> str | None:
        item = await self._redis.blpop([self.name], timeout=timeout_s)
        return item[1] if item is not None else None
