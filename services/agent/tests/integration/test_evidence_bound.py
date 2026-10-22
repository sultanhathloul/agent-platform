"""The evidence fetch under one bound (timeout budget PR, I2, ADR-0255; the PR #12 review,
decision 10).

After the capabilities, the text of their sources is fetched under min(config/turn.yaml
`evidence_fetch_s`, what remains − answer_reserve_s); past it the sources not fetched stay without
text (the existing failure path) and the answer goes on. Tried against a document server that hangs,
trickles or floods `fetch` (tests/fixtures/doc_ops_mcp.py, timeout_s 10, so the step's 1 s bound
decides), while another user's search on the same server runs: that user's time and the event-loop
lag are asserted. Timings are printed for docs/PROGRESS.md (rule 11)."""

import asyncio
import dataclasses
import time
import uuid

import pytest

from app.graph.deps import TurnContext
from app.graph.evidence import load_evidence
from app.identity import Principal
from app.mcp.client import _ATTEMPT_MARGIN_S as ATTEMPT_MARGIN_S
from app.observability.otel import tracer
from app.rag.backend import DocumentScope
from app.rag.collections import session_collection
from app.resilience import deadline
from app.resilience.turn import TurnConfig
from tests.fixtures.doc_ops_mcp import DocumentOpsMCPServer, connected
from tests.fixtures.timing import LagProbe

pytestmark = pytest.mark.integration

EVIDENCE_S = 1.0
EDGE_S = 0.5
LAG_BOUND_S = 0.1
TURN = TurnConfig(
    deadline_s=10.0, answer_reserve_s=0.0, post_answer_reserve_s=0.0, persist_s=0.5,
    evidence_fetch_s=EVIDENCE_S,
    gateway_margin_s=1.0,
)  # fmt: skip


@pytest.fixture
async def ops(settings):
    server = DocumentOpsMCPServer()
    server.start()
    client, backend = await connected(
        server, hash_key=settings.content_hash_key, timeout_s=10.0, max_concurrency=8
    )
    yield server, backend
    await client.stop()
    server.stop()


def _state(conversation: str, refs: list[str]) -> dict:
    sources = [
        {
            "ref": ref,
            "backend": "mcp",
            "collection": session_collection(conversation),
            "title": "ملف",
            "page": 1,
        }
        for ref in refs
    ]
    return {
        "conversation_id": conversation,
        "selected_kbs": [],
        "locale": "ar",
        "user_message": "سؤال",
        "capability_outputs": [{"capability": "docs", "sources": sources}],
    }


async def _evidence(services, user: str) -> tuple[list, float]:
    conversation = str(uuid.uuid4())
    ctx = TurnContext(
        services=services,
        principal=Principal(user_id=user, permissions=("cap:general", "cap:docs")),
        request_id="r",
        turn_id=uuid.uuid4().hex,
        conversation_id=conversation,
    )
    started = time.monotonic()
    with tracer.start_as_current_span("test.evidence"), deadline.within(TURN.deadline_s):
        retrieved = await load_evidence(_state(conversation, ["r1", "r2"]), ctx)
    return retrieved, time.monotonic() - started


async def _search(backend, user: str) -> float:
    scope = DocumentScope.session(tenant_id="default", user_id=user, conversation_id=str(uuid.uuid4()))
    started = time.monotonic()
    assert await backend.search("سؤال", scope, 8) == []
    return time.monotonic() - started


@pytest.mark.parametrize("kind", ["hung", "trickle", "flood"])
async def test_the_evidence_fetch_ends_by_its_bound_and_the_answer_goes_on(services, ops, spans, kind):
    server, backend = ops
    timed = dataclasses.replace(services, turn=TURN, backends={**services.backends, "mcp": backend})
    fetched, quick = await _evidence(timed, f"u-ev-q-{uuid.uuid4().hex[:6]}")
    assert [r["id"] for r in fetched] == ["r1", "r2"]  # the server answers: both sources get text
    alone = await _search(backend, "u-ev-other")
    server.mode["fetch"] = kind
    try:
        with LagProbe() as probe:
            mine = asyncio.create_task(_evidence(timed, f"u-ev-{uuid.uuid4().hex[:6]}"))
            while not server.calls["fetch"] and not mine.done():  # bounded by the step's own 1 s
                await asyncio.sleep(0.02)
            other = await _search(backend, "u-ev-other")
            retrieved, took = await mine
    finally:
        server.mode["fetch"] = "ok"
    assert retrieved == []  # the sources stay without text; the answer goes on with what it has
    events = [e for s in spans.get_finished_spans() if s.name == "test.evidence" for e in s.events]
    assert [e for e in events if e.name == "evidence.unavailable"], events
    if kind == "flood":
        assert took < EVIDENCE_S, took  # refused at its byte cap, unparsed
    else:
        # The MCP attempt ends _ATTEMPT_MARGIN_S (0.5 s) before the step's bound, as tool_timeout
        # (ADR-0259); the sources it was fetching stay without text.
        assert EVIDENCE_S - ATTEMPT_MARGIN_S - 0.1 <= took <= EVIDENCE_S + EDGE_S, took
    assert other < alone + 0.5, (other, alone)
    assert probe.worst < LAG_BOUND_S, probe.worst
    for _ in range(100):  # the fetch's request was closed at the server
        if server.open_requests["fetch"] == 0:
            break
        await asyncio.sleep(0.02)
    assert server.open_requests["fetch"] == 0
    print(f"10 evidence fetch {kind}: no text after {took:.2f} s (bound {EVIDENCE_S:.0f} s; {quick:.2f} s "
          f"when it answers); another user's search {other * 1000:.0f} ms ({alone * 1000:.0f} ms alone); "
          f"loop lag max {probe.worst * 1000:.1f} ms")  # fmt: skip
