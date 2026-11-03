"""Rule 12 for My files (my-files PR, CLAUDE.md rule 12h; ADR-0443): file_search against an index whose embedding
hangs, trickles, or a library that floods (thousands of one user's files), each while another user's search and
page run. That user's platform-side time and the event-loop lag are asserted; the timings are printed for
docs/PROGRESS.md (rule 11)."""

import asyncio
import dataclasses
import time
import uuid

import pytest

from app.identity import Principal
from app.rag.backend import DocumentScope
from app.rag.types import Owner
from tests.fixtures.backends import Harness
from tests.fixtures.timing import LagProbe

pytestmark = pytest.mark.integration

TOOL_S = 1.0  # file_search's bound in these tests (40 s in its ToolSpec)
OTHER_S = 2.0  # the other user's search and page, platform-side (mock embeddings)
LAG_BOUND_S = 0.1
FLOOD = 3000  # one user's files, past both caps (search.max_files 500, list.scan_max 2000)


def _principal() -> Principal:
    return Principal(user_id=f"u-fb-{uuid.uuid4().hex[:6]}", permissions=("cap:general", "cap:files"))


class _Index:
    """The shared retriever, made to hang or trickle only for a query that says so."""

    def __init__(self, inner):
        self._inner = inner

    async def retrieve(self, owner, collections, question, **kwargs):
        if "HANG" in question:
            await asyncio.Event().wait()
        if "TRICKLE" in question:
            for _ in range(6):
                await asyncio.sleep(0.1)
        return await self._inner.retrieve(owner, collections, question, **kwargs)


async def _indexed(services, principal) -> str:
    conversation = str(uuid.uuid4())
    scope = DocumentScope.session(tenant_id=principal.tenant_id, user_id=principal.user_id, conversation_id=conversation)
    harness = Harness("internal", services)
    document = await harness.upload(scope, "تقرير-المختبر.pdf", ["تقرير مختبر\nالهيموجلوبين 13.2 غرام لكل ديسيلتر\nضمن المعدل"])
    assert await harness.ingest(document) == "indexed"
    return document


async def _flood(services, principal, count: int) -> None:
    """`count` indexed files of one user, one conversation each, straight into the records."""
    async with services.db.connection() as conn:
        await conn.execute(
            "INSERT INTO agent.attachments (conversation_id, tenant_id, user_id, name, mime, kind, size, object_key, "
            "collection, status) "
            "SELECT c, %s, %s, 'ملف-' || n || '.pdf', 'application/pdf', 'pdf', 1000, 'flood/' || c, 'sess_' || c, "
            "'indexed' FROM (SELECT gen_random_uuid() AS c, n FROM generate_series(1, %s) AS n) AS s",
            (principal.tenant_id, principal.user_id, count),
        )


async def _other(services, other) -> tuple[float, str]:
    """The other user's search and page, timed together."""
    started = time.monotonic()
    result = await services.broker.execute(
        "file_search", {"query": "الهيموجلوبين"}, caller="files", principal=other, conversation_id=str(uuid.uuid4())
    )
    await services.files.page(Owner(tenant_id=other.tenant_id, user_id=other.user_id), query="تقرير")
    return time.monotonic() - started, result.status


@pytest.mark.parametrize("kind", ["hung", "trickle", "flood"])
async def test_file_search_ends_by_its_bound_and_another_user_is_not_stalled(services, monkeypatch, kind):
    spec = services.tools.get("file_search")
    monkeypatch.setitem(services.tools._tools, "file_search", (dataclasses.replace(spec, timeout_s=TOOL_S), "files"))
    monkeypatch.setattr(services.files, "_retriever", _Index(services.files._retriever))
    stalled, other = _principal(), _principal()
    await _indexed(services, other)
    await _indexed(services, stalled)  # a search that reaches the index (one with no file never does)
    if kind == "flood":
        await _flood(services, stalled, FLOOD)
    query = {"hung": "HANG الهيموجلوبين", "trickle": "TRICKLE الهيموجلوبين", "flood": "الهيموجلوبين"}[kind]

    with LagProbe() as probe:
        started = time.monotonic()
        stalled_call = asyncio.create_task(
            services.broker.execute(
                "file_search", {"query": query}, caller="files", principal=stalled, conversation_id=str(uuid.uuid4())
            )
        )
        await asyncio.sleep(0.05)
        other_s, other_status = await _other(services, other)
        result = await stalled_call
        stalled_s = time.monotonic() - started
    print(f"\n[{kind}] stalled user {stalled_s:.3f} s ({result.status} {result.error}); other user {other_s:.3f} s; "
          f"lag {probe.worst * 1000:.1f} ms")
    assert other_status == "ok" and other_s < OTHER_S
    assert probe.worst < LAG_BOUND_S
    if kind == "hung":
        assert result.status == "error" and result.error == "tool_timeout"
        assert stalled_s < TOOL_S + 0.5
    else:
        assert result.status == "ok" and stalled_s < TOOL_S + 0.5
    if kind == "flood":
        # Capped: the search reads the newest search.max_files (the flood's, newer than the real file), the page
        # scans list.scan_max.
        assert len(await services.files.searchable(Owner(tenant_id=stalled.tenant_id, user_id=stalled.user_id))) == 500
        entries, _ = await services.files.page(Owner(tenant_id=stalled.tenant_id, user_id=stalled.user_id), query="ملف")
        assert len(entries) == services.files.config.list.page_size
