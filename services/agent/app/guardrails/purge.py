"""One-time purge of what was stored before the DATA guard existed (D4, phase 03 B1).

    python -m app.guardrails.purge [--dry-run]      (make purge-pre-guard [DRY_RUN=1])

- memory_items: every row is checked as a DATA `memory_candidate` for its own user and
  tenant; a row the policies would block is deleted, with the recorded context packages
  that used it (D5).
- summaries: the summary in each thread's latest checkpoint is checked as a DATA
  `summary`; a redaction is written back in place through a state update, under the
  conversation's turn lock; a block removes the summary.
- Checkpoint history and messages are not touched: development data is fake, and
  production starts with the guard (D4).

Idempotent: a second run finds nothing, because deleted rows are gone and a redacted
summary no longer matches. A fail-closed policy that cannot be evaluated stops the run
before anything is changed for that row. `--dry-run` prints the same counts and
changes nothing."""

import argparse
import asyncio
import json
import sys
import uuid
from dataclasses import asdict, dataclass

from app.graph.deps import Services
from app.guardrails.checkpoints import Checkpoint
from app.guardrails.schema import DataPayload
from app.identity import Principal

LOCK_WAIT_S = 10.0


class PurgeAborted(RuntimeError):
    pass


@dataclass
class PurgeReport:
    dry_run: bool
    memory_items_scanned: int = 0
    memory_items_deleted: int = 0
    threads_scanned: int = 0
    summaries_scanned: int = 0
    summaries_redacted: int = 0
    summaries_removed: int = 0


async def _verdict(services: Services, text: str, target: str, user_id: str, tenant_id: str):
    outcome = await services.guard.evaluate(
        Checkpoint.DATA,
        DataPayload(text=text, target=target),
        principal=Principal(user_id=user_id, tenant_id=tenant_id),
    )
    if outcome.unavailable:
        raise PurgeAborted(f"policy {outcome.unavailable} could not be evaluated; nothing more was changed")
    return outcome


async def purge(services: Services, *, dry_run: bool) -> PurgeReport:
    report = PurgeReport(dry_run=dry_run)

    async with services.db.connection() as conn:
        rows = await (
            await conn.execute(
                "SELECT id, tenant_id, user_id, text FROM agent.memory_items ORDER BY created_at"
            )
        ).fetchall()
    doomed = []
    for row in rows:
        report.memory_items_scanned += 1
        outcome = await _verdict(services, row["text"], "memory_candidate", row["user_id"], row["tenant_id"])
        if outcome.blocked:
            doomed.append(str(row["id"]))
    report.memory_items_deleted = len(doomed)
    if doomed and not dry_run:
        async with services.db.connection() as conn, conn.transaction():
            await services.memory.store.delete_ids(doomed, conn=conn)

    async with services.db.connection() as conn:
        threads = [
            r["thread_id"]
            for r in await (
                await conn.execute(
                    "SELECT DISTINCT thread_id FROM agent.checkpoints WHERE checkpoint_ns = ''"
                )
            ).fetchall()
        ]
    for thread_id in threads:
        try:
            uuid.UUID(thread_id)
        except ValueError:
            continue  # not a conversation (conversation ids are UUIDs, ADR-0050)
        report.threads_scanned += 1
        config = {"configurable": {"thread_id": thread_id}}
        values = (await services.graph.aget_state(config)).values or {}
        summary = values.get("summary")
        if not summary or values.get("user_id") is None:
            continue
        report.summaries_scanned += 1
        outcome = await _verdict(services, summary, "summary", values["user_id"], values["tenant_id"])
        if outcome.blocked:
            replacement, counter = None, "summaries_removed"
        elif outcome.payload.text != summary:
            replacement, counter = outcome.payload.text, "summaries_redacted"
        else:
            continue
        setattr(report, counter, getattr(report, counter) + 1)
        if dry_run:
            continue
        lease = await services.locks.acquire(thread_id, wait_s=LOCK_WAIT_S)
        if lease is None:
            raise PurgeAborted(f"conversation {thread_id} stayed locked by a turn; run the purge again")
        try:
            # As the graph's last node, so the thread is left with no pending task (as retract does).
            await services.graph.aupdate_state(config, {"summary": replacement}, as_node="write_memory")
        finally:
            await lease.release()
    return report


async def _main(dry_run: bool) -> int:
    from app.main import build_services, start_services, stop_services
    from app.settings import get_settings

    services = await start_services(build_services(get_settings()))
    try:
        report = await purge(services, dry_run=dry_run)
    except PurgeAborted as exc:
        print(f"purge aborted: {exc}", file=sys.stderr)
        return 1
    finally:
        await stop_services(services)
    print(json.dumps(asdict(report), indent=2))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="print the counts and change nothing")
    sys.exit(asyncio.run(_main(parser.parse_args().dry_run)))
