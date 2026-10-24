"""Generator of phase05_docs_conversation.json: the LangGraph checkpoint rows of one docs
conversation as the agent persisted them before phase 06 (phase 06, G9). It is kept for
reproduction only; it runs against that code, not this tree's, and no test imports it.

How it was run (2026-10-24), as generate_phase04.py describes, with these differences:

1. Code: commit 09464df (main before phase 06: phase 05, the conversation history, the timeout
   budget and the platform settings merged), as a detached worktree: git worktree add <wt> 09464df
2. Image: that worktree's agent test stage, with the reference servers' context:
       docker build --target test --build-context mcpref=<wt>/infra/mcp-ref -t agent-p05-test <wt>/services/agent
3. Database: agent_fixture_p05, created as agent_fixture_p04 was.
4. Run as generate_phase04.py's step 4, with agent-p05-test, this file, AGENT_TEST_DATABASE_URL on
   agent_fixture_p05 and <wt>/config.

What it does: the phase 04 conversation again (one PDF indexed by the internal backend; turn 1
«كم مدة إجازة الأمومة؟» with the file, routed to docs, its reply replaced and citing page 2;
turn 2 «شكرا», the router's reply replaced to choose general). Since the conversation history
(ADR-0193) a reply records its citations on the message, so this fixture checks that they reopen
unchanged too. Nothing in it knows of skills.
"""

import asyncio
import json
import os
import sys
import uuid
from datetime import date, datetime
from pathlib import Path

import psycopg

from app.api.runner import TurnRunner
from app.api.schemas import TurnRequest
from app.identity import Principal
from app.main import build_services, start_services, stop_services
from app.rag.backend import DocumentScope
from app.settings import Settings
from tests.fixtures.backends import Harness
from tests.fixtures.documents import LEAVE_REGULATION
from tests.fixtures.replace import replacing

CONVERSATION_ID = "0b5e0505-0d0c-4f05-8a11-c0ffee000505"
USER_ID = "u-p05-fixture"
TENANT_ID = "default"
PERMISSIONS = ("cap:general", "cap:docs")
DOCUMENT_NAME = "لائحة الإجازات.pdf"
TURNS = [
    # (question, replaced role, replaced reply, attach the document)
    (
        "كم مدة إجازة الأمومة؟",
        "reasoning",
        "تُمنح إجازة الأمومة لمدة عشرة أسابيع براتب كامل، وفق المادة الثالثة.",
        True,
    ),
    ("شكرا", "router", '{"capabilities": ["general"], "complexity": "low", "risk": "low"}', False),
]
TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")
SCHEMA = "agent"


def _settings(database_url: str) -> Settings:
    """As the root conftest's `settings` fixture builds them at 09464df."""
    env = os.environ
    return Settings(
        litellm_base_url=env["LITELLM_BASE_URL"],
        litellm_api_key=env["LITELLM_API_KEY"],
        agent_internal_token="test-internal-token",
        config_dir=Path(env["CONFIG_DIR"]),
        user_id_hash_salt="test-salt",
        content_hash_key="test-content-hash-key",
        observability_capture_content=True,
        otel_sdk_disabled=True,
        redis_url=env["REDIS_URL"],
        redis_namespace=f"agent-fixture-{uuid.uuid4().hex[:8]}",
        presidio_url=env["PRESIDIO_URL"],
        minio_endpoint=env["MINIO_ENDPOINT"],
        minio_access_key=env["MINIO_ACCESS_KEY"],
        minio_secret_key=env["MINIO_SECRET_KEY"],
        minio_sweeper_access_key=env["MINIO_SWEEPER_ACCESS_KEY"],
        minio_sweeper_secret_key=env["MINIO_SWEEPER_SECRET_KEY"],
        database_url=database_url,
    )


def _value(value):
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _dump(database_url: str) -> dict:
    out: dict = {"tables": {}, "columns": {}}
    with psycopg.connect(database_url) as conn:
        for table in TABLES:
            columns = conn.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
                (SCHEMA, table),
            ).fetchall()
            out["columns"][table] = {name: kind for name, kind in columns}
            names = [name for name, _ in columns]
            order = [
                n
                for n in ("checkpoint_ns", "checkpoint_id", "channel", "version", "task_id", "idx")
                if n in names
            ]
            rows = conn.execute(
                f"SELECT {', '.join(names)} FROM {SCHEMA}.{table} "
                f"WHERE thread_id = %s ORDER BY {', '.join(order)}",
                (CONVERSATION_ID,),
            ).fetchall()
            out["tables"][table] = [{n: _value(v) for n, v in zip(names, row, strict=True)} for row in rows]
        out["checkpoint_migrations"] = [
            row[0]
            for row in conn.execute(f"SELECT v FROM {SCHEMA}.checkpoint_migrations ORDER BY v").fetchall()
        ]
        owner = conn.execute(
            f"SELECT conversation_id, user_id, tenant_id FROM {SCHEMA}.conversation_owners "
            "WHERE conversation_id = %s",
            (CONVERSATION_ID,),
        ).fetchall()
        out["conversation_owners"] = [
            {k: _value(v) for k, v in zip(("conversation_id", "user_id", "tenant_id"), r, strict=True)}
            for r in owner
        ]
    return out


async def main(target: Path) -> None:
    database_url = os.environ["AGENT_TEST_DATABASE_URL"]
    services = await start_services(build_services(_settings(database_url)))
    try:
        principal = Principal(user_id=USER_ID, tenant_id=TENANT_ID, permissions=PERMISSIONS)
        harness = Harness("internal", services)
        scope = DocumentScope.session(tenant_id=TENANT_ID, user_id=USER_ID, conversation_id=CONVERSATION_ID)
        document = await harness.upload(scope, DOCUMENT_NAME, ["\n".join(p) for p in LEAVE_REGULATION])
        assert await harness.ingest(document) == "indexed"

        turns, citations, digests = [], [], []
        for question, role, reply, attach in TURNS:
            replaced, _ = replacing(services, {role: reply})
            request = TurnRequest(
                conversation_id=CONVERSATION_ID,
                principal=principal,
                message=question,
                attachment_ids=[document] if attach else [],
                locale="ar",
            )
            events = [e async for e in TurnRunner(replaced).stream(request, f"req-p05-fixture-{len(turns)}")]
            done = events[-1]
            assert done.type == "done", done
            turns.append({"question": question, "answer": done.data.answer, "turn_id": done.turn_id})
            digests.append(done.data.state_digest.model_dump(mode="json"))
            citations.append([c.model_dump(mode="json") for c in done.data.citations])
        assert digests[0]["capabilities"] == ["docs"] and citations[0], (digests, citations)

        snapshot = await services.graph.aget_state({"configurable": {"thread_id": CONVERSATION_ID}})
        state_keys = sorted(snapshot.values)
    finally:
        await stop_services(services)

    dumped = _dump(database_url)
    import importlib.metadata as md

    fixture = {
        "generated_from": "09464df",
        "generated_on": "2026-10-24",
        "langgraph": {
            p: md.version(p)
            for p in (
                "langgraph",
                "langgraph-checkpoint",
                "langgraph-checkpoint-postgres",
                "langgraph-prebuilt",
            )
        },
        "conversation_id": CONVERSATION_ID,
        "user_id": USER_ID,
        "tenant_id": TENANT_ID,
        "attachment_id": document,
        "attachment_name": DOCUMENT_NAME,
        "turns": turns,
        "citations": citations,
        "state_digests": digests,
        "state_keys": state_keys,
        "schema": {"schema": SCHEMA, "tables": list(TABLES), "columns": dumped["columns"]},
        "tables": dumped["tables"],
        "checkpoint_migrations": dumped["checkpoint_migrations"],
        "conversation_owners": dumped["conversation_owners"],
    }
    target.write_text(json.dumps(fixture, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print({t: len(r) for t, r in dumped["tables"].items()}, "attachment", document)


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1])))
