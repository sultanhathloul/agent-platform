#!/usr/bin/env bash
# Least privilege on connections (fix session 2026-10-18, item 16, ADR-0242): PUBLIC keeps no CONNECT
# on the business, keycloak, langfuse and agent_test databases; each is granted back to the role
# that uses it (its owner). The reference RAG server's role connects only to its own: `app`, where
# its schema is, and `rag_ref_test`, the database `make test` runs that server on as that role,
# with the vector extension and a `rag_ref` schema it owns. Idempotent: the one-shot
# `postgres-roles` service runs it after rag-ref.sh on every `make up` and `make test-agent`.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --dbname postgres <<'SQL'
SELECT 'CREATE DATABASE rag_ref_test'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'rag_ref_test') \gexec
REVOKE CONNECT ON DATABASE rag_ref_test FROM PUBLIC;
GRANT CONNECT ON DATABASE rag_ref_test TO rag_ref;
SELECT format('REVOKE CONNECT ON DATABASE %I FROM PUBLIC', datname),
       format('GRANT CONNECT ON DATABASE %I TO %I', datname, pg_get_userbyid(datdba))
FROM pg_database WHERE datname IN ('business', 'keycloak', 'langfuse', 'agent_test') \gexec
SQL
psql -v ON_ERROR_STOP=1 --dbname rag_ref_test <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS rag_ref AUTHORIZATION rag_ref;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL
echo "postgres: CONNECT revoked from PUBLIC on business, keycloak, langfuse, agent_test; rag_ref_test ready"
