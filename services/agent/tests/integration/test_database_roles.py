"""Least privilege on connections (ADR-0242): the reference RAG server's role connects only to its
own databases (`app`, where its schema is, and `rag_ref_test`, where `make test` runs it), never to
business, keycloak, langfuse or agent_test, whose CONNECT PUBLIC no longer holds; and in its own
database it owns its schema and nothing of the agent's."""

import os
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

from tests.fixtures.rag_ref import rag_ref_database_url

pytestmark = pytest.mark.integration

OTHERS = ("business", "keycloak", "langfuse", "agent_test")


def _on(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{database}"))


@pytest.mark.parametrize("database", OTHERS)
def test_rag_ref_cannot_connect_to_another_database(database):
    with pytest.raises(psycopg.OperationalError) as refused:
        psycopg.connect(_on(rag_ref_database_url(), database), connect_timeout=5).close()
    assert "permission denied" in str(refused.value) or "CONNECT" in str(refused.value)


def test_rag_ref_connects_to_its_own_and_owns_only_its_schema():
    with psycopg.connect(rag_ref_database_url(), connect_timeout=5) as conn:
        owner = conn.execute(
            "SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'rag_ref'"
        ).fetchone()
        assert owner == ("rag_ref",)
        assert conn.execute("SELECT has_schema_privilege('rag_ref', 'public', 'CREATE')").fetchone() == (
            False,
        )


def test_the_app_role_keeps_its_test_database():
    url = os.environ.get("AGENT_TEST_DATABASE_URL")
    assert url, "make test-agent sets AGENT_TEST_DATABASE_URL"
    with psycopg.connect(url, connect_timeout=5) as conn:
        assert conn.execute("SELECT current_database()").fetchone() == ("agent_test",)
