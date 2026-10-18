"""Generator of phase04_docs_conversation.json: the LangGraph checkpoint rows of one docs
conversation as the phase 04 agent persisted them. It is kept for reproduction only; it runs
against the phase 04 code, not this tree's, and no test imports it.

How it was run (2026-10-18):

1. Code: commit 30302d2 (the merge of PR #4, phase 04) checked out as a detached worktree:
       git worktree add <wt> 30302d2
2. Image: the phase 04 agent test stage, built from that worktree:
       docker build --target test -t agent-p04-test <wt>/services/agent
3. Database: a database of its own on the compose Postgres, owned by the app role:
       docker compose exec -T postgres psql -U postgres -c 'CREATE DATABASE agent_fixture_p04 OWNER app'
       docker compose exec -T postgres psql -U postgres -d agent_fixture_p04 -c 'CREATE EXTENSION vector'
4. Run, on the compose network, with the environment the phase 04 `agent-tests` service gets
   (its docker-compose.yml, x-agent-env plus the agent-tests entries) except the database URL;
   the mock LiteLLM is the `litellm-mock` service (infra/litellm/config.mock.yaml, unchanged
   since phase 04):
       docker run --rm --network agent-platform_default \\
         -v <repo>/config:/srv/config:ro -v <this dir>:/out \\
         -e CONFIG_DIR=/srv/config -e PRESIDIO_URL=http://presidio-analyzer:3000 \\
         -e LITELLM_BASE_URL=http://litellm-mock:4000 -e LITELLM_API_KEY=$LITELLM_MASTER_KEY \\
         -e REDIS_URL=redis://:$REDIS_PASSWORD@redis:6379/15 -e MINIO_ENDPOINT=minio:9000 \\
         -e MINIO_ACCESS_KEY=... -e MINIO_SECRET_KEY=... \\
         -e MINIO_SWEEPER_ACCESS_KEY=... -e MINIO_SWEEPER_SECRET_KEY=... \\
         -e RERANK_ENABLED=false \\
         -e AGENT_TEST_DATABASE_URL=postgresql://app:$APP_DB_PASSWORD@postgres:5432/agent_fixture_p04 \\
         -v <this file>:/app/generate_phase04.py:ro \\
         agent-p04-test python generate_phase04.py /out/phase04_docs_conversation.json

   <repo>/config is the phase 04 worktree's config directory.

What it does, as the phase 04 docs tests do (app/capabilities/docs/tests/test_docs.py): a
user's conversation holds one PDF (tests/fixtures/documents.py LEAVE_REGULATION) uploaded and
indexed by the internal DocumentBackend; turn 1 asks «كم مدة إجازة الأمومة؟» with the file
attached, so the attachments rule routes it to docs; the reasoning reply is replaced after a real
call through Context Assembly and the mock LiteLLM (ADR-0075), and page attribution cites page 2.
Turn 2, «شكرا», goes to the router model, whose reply is replaced to choose `general`. The
checkpoint tables' rows of the thread are then dumped: every column; bytea as hex; jsonb as
JSON; timestamps as ISO 8601.
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

CONVERSATION_ID = "0b5e0404-0d0c-4f04-8a11-c0ffee000404"
USER_ID = "u-p04-fixture"
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
    """As the phase 04 root conftest's `settings` fixture builds them."""
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
        scope = DocumentScope(tenant_id=TENANT_ID, user_id=USER_ID, conversation_id=CONVERSATION_ID)
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
            events = [e async for e in TurnRunner(replaced).stream(request, f"req-p04-fixture-{len(turns)}")]
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
        "generated_from": "30302d2",
        "generated_on": "2026-10-18",
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
