"""One-time purge of what was stored before the DATA guard existed (D4, phase 03 B1).

    python -m app.guardrails.purge [--dry-run]      (make purge-pre-guard [DRY_RUN=1])

- memory_items: every row is checked as a DATA `memory_candidate` for its own user and
  tenant; a row the policies would block is deleted, a row they would redact is rewritten
  in place with a fresh vector, and in both cases the recorded context packages that used
  it go with its old text (D5, ADR-0112).
- summaries: the summary in each thread's latest checkpoint is checked as a DATA
  `summary`, read and written while this run holds the conversation's turn lock, so a
  turn's fold can never land between the two (ADR-0112); a redaction is written back in
  place through a state update, a block removes the summary.
- Only global and tenant policies decide: the purge acts for no session, so it holds no
  policy role, and a role exception never spares a stored row (ADR-0112).
- Checkpoint history and messages are not touched: development data is fake, and
  production starts with the guard (D4).

Idempotent: a second run finds nothing, because deleted rows are gone and redacted text
no longer matches. A fail-closed policy that cannot be evaluated stops the run before
anything is changed for that row. The counts of a real run are the rows it changed;
`--dry-run` prints the rows it would change and changes nothing."""

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
    memory_items_redacted: int = 0
    threads_scanned: int = 0
    summaries_scanned: int = 0
    summaries_redacted: int = 0
    summaries_removed: int = 0


async def _verdict(services: Services, text: str, target: str, user_id: str, tenant_id: str):
    # No policy roles: global and tenant policies only (ADR-0112).
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
    await _purge_memory(services, report, dry_run=dry_run)
    await _purge_summaries(services, report, dry_run=dry_run)
    return report


async def _purge_memory(services: Services, report: PurgeReport, *, dry_run: bool) -> None:
    async with services.db.connection() as conn:
        rows = await (
            await conn.execute(
                "SELECT id, tenant_id, user_id, text FROM agent.memory_items ORDER BY created_at"
            )
        ).fetchall()
    doomed, rewrites = [], []
    for row in rows:
        report.memory_items_scanned += 1
        outcome = await _verdict(services, row["text"], "memory_candidate", row["user_id"], row["tenant_id"])
        if outcome.blocked:
            doomed.append(str(row["id"]))
        elif outcome.payload.text != row["text"]:
            rewrites.append((str(row["id"]), outcome.payload.text))
    if dry_run:
        report.memory_items_deleted, report.memory_items_redacted = len(doomed), len(rewrites)
        return
    vectors = [await services.embeddings.embed(text) for _, text in rewrites]
    async with services.db.connection() as conn, conn.transaction():
        if doomed:
            report.memory_items_deleted = await services.memory.store.delete_ids(doomed, conn=conn)
        for (item_id, text), vector in zip(rewrites, vectors, strict=True):
            report.memory_items_redacted += await services.memory.store.redact_item(
                item_id, text, vector, conn=conn
            )


async def _purge_summaries(services: Services, report: PurgeReport, *, dry_run: bool) -> None:
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
        lease = await services.locks.acquire(thread_id, wait_s=LOCK_WAIT_S)
        if lease is None:
            raise PurgeAborted(f"conversation {thread_id} stayed locked by a turn; run the purge again")
        try:
            await _purge_summary(services, report, thread_id, dry_run=dry_run)
        finally:
            await lease.release()


async def _purge_summary(services: Services, report: PurgeReport, thread_id: str, *, dry_run: bool) -> None:
    """Under the conversation's lock: the summary read is the one written over."""
    config = {"configurable": {"thread_id": thread_id}}
    values = (await services.graph.aget_state(config)).values or {}
    summary = values.get("summary")
    if not summary or values.get("user_id") is None:
        return
    report.summaries_scanned += 1
    outcome = await _verdict(services, summary, "summary", values["user_id"], values["tenant_id"])
    if outcome.blocked:
        replacement, counter = None, "summaries_removed"
    elif outcome.payload.text != summary:
        replacement, counter = outcome.payload.text, "summaries_redacted"
    else:
        return
    if not dry_run:
        # As the graph's last node, so the thread is left with no pending task (as retract does).
        await services.graph.aupdate_state(config, {"summary": replacement}, as_node="write_memory")
        written = (await services.graph.aget_state(config)).values.get("summary")
        if written != replacement:
            raise PurgeAborted(f"conversation {thread_id}: the summary was not written")
    setattr(report, counter, getattr(report, counter) + 1)


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
