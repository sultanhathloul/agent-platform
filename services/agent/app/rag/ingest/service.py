"""The ingest service (owner decision R4): `python -m app.rag.ingest.service`, the agent's image in
a container of its own with a hard memory limit, so a chat turn never shares a process with a
parser. It takes attachment ids from the Redis queue the agent fills, runs the pipeline (each
file parsed in a child process with CPU, memory and time limits, app/rag/ingest/sandbox.py),
re-queues every pending attachment at start (ADR-0130), and runs the orphan sweeper (owner
decision S4). It answers its health port once it is taking jobs."""

import asyncio
import contextlib
import logging
import signal
from datetime import timedelta

from app.attachments.limits import load_upload_limits
from app.attachments.objects import ObjectStore
from app.attachments.store import AttachmentStore
from app.attachments.sweeper import OrphanSweeper
from app.context.tokens import default_counter
from app.db import Database
from app.models.config import load_models_config
from app.models.embeddings import EmbeddingGateway
from app.observability.otel import setup_tracing, shutdown_tracing
from app.platform_settings.setup import build_defaults
from app.platform_settings.store import SettingsStore
from app.rag.config import load_docs_config
from app.rag.ingest.sandbox import MemoryBudgetExceeded, check_memory_budget
from app.rag.setup import build_documents
from app.resilience.turn import load_turn_config
from app.settings import Settings, get_settings

log = logging.getLogger("app.rag.ingest.service")


async def _health(port: int) -> asyncio.base_events.Server:
    async def answer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        with contextlib.suppress(Exception):
            await reader.read(1024)
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
            await writer.drain()
        writer.close()

    return await asyncio.start_server(answer, "0.0.0.0", port)


async def run(settings: Settings, stop: asyncio.Event) -> None:
    from redis.asyncio import Redis

    docs = load_docs_config(settings.config_dir)
    try:
        log.info(check_memory_budget(docs.ingest.workers, docs))
    except MemoryBudgetExceeded as exc:
        log.error("%s", exc)
        raise SystemExit(1) from None
    db = Database(settings.database_url, max_size=4)
    await db.open()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    models_config = load_models_config(settings.config_dir)
    attachments = AttachmentStore(db)

    def objects(key: str, secret: str) -> ObjectStore:
        return ObjectStore(
            settings.minio_endpoint, key, secret, settings.uploads_bucket, secure=settings.minio_secure
        )

    documents = build_documents(
        settings,
        db=db,
        redis=redis,
        embeddings=EmbeddingGateway(settings, models_config),
        models_config=models_config,
        counter=default_counter(),
        attachments=attachments,
        objects=objects(settings.minio_access_key, settings.minio_secret_key),
    )
    sweeping = documents.config.orphan_sweeper
    sweeper = OrphanSweeper(
        objects(settings.minio_sweeper_access_key, settings.minio_sweeper_secret_key),
        attachments,
        min_age=timedelta(minutes=sweeping.min_age_minutes),
    )
    # The administrator's overrides (ADR-0276): read, never seeded, reloaded on the agent's notice.
    platform = SettingsStore(
        db,
        build_defaults(
            settings,
            turn=load_turn_config(settings.config_dir),
            models_config=models_config,
            upload=load_upload_limits(settings.config_dir),
            register_response_s=documents.config.register_response_s,
        ),
        redis,
        namespace=settings.redis_namespace,
    )
    await platform.start(seed=False)
    documents.worker.settings = lambda: platform.current
    await documents.worker.start()
    sweep = asyncio.create_task(sweeper.run(timedelta(minutes=sweeping.interval_minutes)))
    health = await _health(settings.ingest_health_port)
    log.info("ingest service ready: %d workers", documents.config.ingest.workers)
    try:
        await stop.wait()
    finally:
        health.close()
        sweep.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await sweep
        await documents.worker.stop()
        await platform.stop()
        await redis.aclose()
        await db.close()


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    setup_tracing(enabled=not settings.otel_sdk_disabled)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        await run(settings, stop)
    finally:
        shutdown_tracing()


if __name__ == "__main__":
    asyncio.run(main())
