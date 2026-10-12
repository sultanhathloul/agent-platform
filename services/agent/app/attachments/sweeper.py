"""The orphan sweeper (owner decision S4). The gateway stores an upload in MinIO before it
registers it with the agent; when the registration fails and the gateway cannot discard the
object (a storage outage, a crash between the two steps), the object stays without a record.
The sweeper removes every object of the uploads bucket that no attachment record names and
that is older than `min_age_minutes`, so an upload still being registered is never touched.
It deletes nothing a record names: the product deletes no registered file."""

import asyncio
import logging
from datetime import timedelta

from app.attachments.objects import ObjectStore, StorageUnavailable
from app.attachments.store import AttachmentStore
from app.observability.otel import tracer

log = logging.getLogger(__name__)

_BATCH = 500


class OrphanSweeper:
    def __init__(self, objects: ObjectStore, attachments: AttachmentStore, *, min_age: timedelta):
        self._objects = objects
        self._attachments = attachments
        self._min_age = min_age

    async def sweep(self) -> dict[str, int]:
        counts = {"seen": 0, "removed": 0, "errors": 0}
        with tracer.start_as_current_span("attachments.sweep") as span:
            keys = await self._objects.keys_older_than(self._min_age)
            counts["seen"] = len(keys)
            for start in range(0, len(keys), _BATCH):
                batch = keys[start : start + _BATCH]
                known = await self._attachments.known_keys(batch)
                for key in batch:
                    if key in known:
                        continue
                    try:
                        await self._objects.delete(key)
                        counts["removed"] += 1
                    except StorageUnavailable:
                        counts["errors"] += 1
            for name, value in counts.items():
                span.set_attribute(f"sweep.{name}", value)
        return counts

    async def run(self, interval: timedelta) -> None:
        while True:
            try:
                counts = await self.sweep()
                if counts["removed"] or counts["errors"]:
                    log.info("orphan sweep: %s", counts)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("orphan sweep failed: %s", type(exc).__name__)
            await asyncio.sleep(interval.total_seconds())
