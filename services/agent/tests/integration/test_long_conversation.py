"""A 60-turn conversation on the mock models (report library PR, owner decision 7, ADR-0437): what a turn
costs as the conversation grows. Every turn's answer context, its platform time and the conversation's
checkpoint are measured; the context must stay within its budgets (the window and the summary are bounded,
the history is not sent whole), the platform time must not grow with the conversation's length, and what a
turn writes to the checkpoint is measured, so a regression is a number. The figures are printed for
docs/PROGRESS.md (`pytest -s`)."""

import dataclasses
import json
import time
import uuid

import pytest

from app.api.runner import TurnRunner
from app.api.schemas import TurnRequest
from app.identity import Principal
from tests.fixtures.replace import ReplacingGateway

pytestmark = pytest.mark.integration

TURNS = 60
TABLE = "\n".join(["المنطقة\tالسكان\tالمساحة"] + [f"منطقة {i}\t{i * 1.1:.1f}\t{i * 100}" for i in range(1, 6)])
FILLER = "هذه فقرة طويلة عن الاقتصاد والتعليم والصحة في المملكة، تُكتب لتكبير المحادثة. " * 12
SUMMARY = "ملخص: سأل المستخدم عن الاقتصاد والتعليم، وأرسل في البداية جدولًا من خمسة صفوف عن المناطق (المنطقة، السكان، المساحة)."


class Measuring(ReplacingGateway):
    def __init__(self, inner, replies):
        super().__init__(inner, replies)
        self.answer_tokens: list[int] = []
        self.model_s = 0.0

    async def call(self, decision, package, *, on_token=None, temperature=None):
        started = time.monotonic()
        try:
            if decision.role == "reasoning":
                self.answer_tokens.append(package.total_tokens())
            return await super().call(decision, package, on_token=on_token, temperature=temperature)
        finally:
            self.model_s += time.monotonic() - started


async def _checkpoint(services, conversation: str) -> dict:
    async with services.db.connection() as conn:
        cursor = await conn.execute(
            "SELECT count(*) AS checkpoints FROM agent.checkpoints WHERE thread_id = %s", (conversation,))
        checkpoints = (await cursor.fetchone())["checkpoints"]
        cursor = await conn.execute(
            "SELECT coalesce(sum(length(blob)), 0) AS bytes, count(*) AS blobs FROM agent.checkpoint_blobs "
            "WHERE thread_id = %s", (conversation,))
        blobs = await cursor.fetchone()
        cursor = await conn.execute(
            "SELECT coalesce(max(length(blob)), 0) AS bytes FROM agent.checkpoint_blobs "
            "WHERE thread_id = %s AND channel = 'messages'", (conversation,))
        messages = (await cursor.fetchone())["bytes"]
    return {"checkpoints": checkpoints, "blob_bytes": blobs["bytes"], "blobs": blobs["blobs"], "messages_blob": messages}


async def test_sixty_turns_keep_the_context_bounded_and_the_turn_time_flat(services):
    gateway = Measuring(services.gateway, {"summarizer": SUMMARY, "extractor": "[]"})
    measured = dataclasses.replace(services, gateway=gateway)
    principal = Principal(user_id=f"u-long-{uuid.uuid4().hex[:6]}", permissions=("cap:general", "cap:recall"))
    conversation = str(uuid.uuid4())
    runner = TurnRunner(measured)
    rows = []
    for turn in range(1, TURNS + 1):
        message = f"خذ هذا الجدول:\n{TABLE}" if turn == 2 else f"السؤال {turn}: {FILLER}"
        request = TurnRequest(conversation_id=conversation, principal=principal, message=message, locale="ar",
                              output="text")
        gateway.model_s = 0.0
        started = time.monotonic()
        admission = await runner.admit(request)
        events = [e async for e in runner.stream(request, f"req-{uuid.uuid4().hex[:6]}", admission=admission)]
        total = time.monotonic() - started
        assert events[-1].type == "done", events[-1]
        rows.append({"turn": turn, "platform_s": round(total - gateway.model_s, 3), "model_s": round(gateway.model_s, 3),
                     "answer_tokens": gateway.answer_tokens[-1], **await _checkpoint(services, conversation)})
    print("\nlong conversation (mock models):")
    for row in rows:
        if row["turn"] in (1, 2, 5, 10, 20, 30, 40, 50, 60):
            print(json.dumps(row, ensure_ascii=False))

    by_turn = {r["turn"]: r for r in rows}
    # The answer's context is the window, the summary and the message: bounded, never the whole history.
    budget = services.assembler.config
    ceiling = budget.blocks["window"].max_tokens + budget.blocks["summary"].max_tokens + 4000
    assert max(r["answer_tokens"] for r in rows) <= ceiling
    assert by_turn[60]["answer_tokens"] <= by_turn[30]["answer_tokens"] * 1.2 + 200
    # The platform's own time does not grow with the conversation's length.
    p10 = sorted(r["platform_s"] for r in rows[5:15])[5]
    p60 = sorted(r["platform_s"] for r in rows[-10:])[5]
    assert p60 <= max(2.0 * p10, p10 + 0.5), (p10, p60)
    # What one turn adds to the checkpoint stays bounded: the latest messages blob is the history once.
    growth = by_turn[60]["messages_blob"] - by_turn[59]["messages_blob"]
    assert 0 < growth <= 3 * len(FILLER.encode()), growth
    # Older checkpoints are pruned after each turn (ADR-0437): their count is flat, and the stored blobs are
    # the history in the few versions the kept checkpoints name (about four), never one copy per turn.
    keep = services.library.history.keep_checkpoints
    assert by_turn[60]["checkpoints"] <= keep and by_turn[30]["checkpoints"] <= keep
    assert by_turn[60]["blob_bytes"] <= 5 * by_turn[60]["messages_blob"], by_turn[60]  # about four versions kept
    # The history itself is whole: every exchange is still there to recall.
    snapshot = await services.graph.aget_state({"configurable": {"thread_id": conversation}})
    assert len(snapshot.values["messages"]) == 2 * TURNS
