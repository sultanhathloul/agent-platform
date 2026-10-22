"""The ingestion worker (ADR-0130): `ingest.workers` tasks in the agent process take
attachment ids from a Redis list and run the pipeline. At start every `pending`
attachment is put back on the queue, so a job interrupted by a crash or a restart runs
again (one agent process is assumed, as for the conversation registry, ADR-0050). A
transient failure is retried with a growing pause until `ingest.max_attempts`, then the
attachment ends `failed` with the transient reason."""

import asyncio
import contextlib
import logging
from collections.abc import Callable

from app.attachments.store import AttachmentStore
from app.platform_settings import effective
from app.rag.config import DocsConfig
from app.rag.ingest.pipeline import IngestPipeline, Retry
from app.rag.ingest.queue import IngestQueue

log = logging.getLogger(__name__)

_POP_TIMEOUT_S = 2


class IngestWorker:
    def __init__(
        self,
        pipeline: IngestPipeline,
        attachments: AttachmentStore,
        config: DocsConfig,
        queue: IngestQueue,
    ):
        self.pipeline = pipeline
        self._attachments = attachments
        self._config = config
        self.queue = queue
        self._tasks: list[asyncio.Task] = []
        # The platform settings in force (the ingest service's store, ADR-0279): each job runs with
        # the snapshot current when it starts, e.g. the embedding batch's ceiling. None: the files'.
        self.settings: Callable[[], effective.Effective | None] = lambda: None

    async def start(self) -> None:
        await self.queue.requeue(await self._attachments.pending_ids())
        for _ in range(self._config.ingest.workers):
            self._tasks.append(asyncio.create_task(self._loop()))
        log.info("ingestion worker started (%d tasks)", len(self._tasks))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()

    async def _loop(self) -> None:
        while True:
            try:
                item = await self.queue.take(_POP_TIMEOUT_S)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("ingest queue unavailable: %s", type(exc).__name__)
                await asyncio.sleep(2)
                continue
            if item is not None:
                with effective.pin(self.settings()):
                    await self._job(item)

    async def _job(self, attachment_id: str) -> str:
        attempt = 0
        while True:
            attempt += 1
            try:
                return await self.pipeline.run(attachment_id)
            except asyncio.CancelledError:
                raise
            except Retry as retry:
                record = await self._attachments.by_id(attachment_id)
                if record is None or record.status != "pending":
                    return "skipped"
                if record.attempts >= self._config.ingest.max_attempts:
                    await self.pipeline.fail(attachment_id, retry.reason)
                    return "failed"
                await asyncio.sleep(min(2**attempt, 30))
            except Exception as exc:  # a defect: the attachment must not stay pending forever
                # The type and the reason code only: a library's message can quote the document (S5).
                log.warning("ingestion of %s failed: %s (ingest_error)", attachment_id, type(exc).__name__)
                await self.pipeline.fail(attachment_id, "ingest_error")
                return "failed"
