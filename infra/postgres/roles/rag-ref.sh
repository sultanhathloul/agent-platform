#!/usr/bin/env bash
# The reference RAG server's database role and schema (phase 05, ADR-0211): a login role of its
# own that owns schema `rag_ref` in database `app` and has no privilege on schema `agent`, so the
# server can read and write its own documents and chunks and nothing of the agent's. Idempotent:
# the one-shot `postgres-roles` service runs it on every `make up`, on a new volume or an old one.
set -euo pipefail
: "${RAG_REF_DB_PASSWORD:?RAG_REF_DB_PASSWORD is not set (see .env.example)}"

psql -v ON_ERROR_STOP=1 -v password="$RAG_REF_DB_PASSWORD" --dbname app <<'SQL'
SELECT format('CREATE ROLE rag_ref LOGIN PASSWORD %L', :'password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'rag_ref') \gexec
SELECT format('ALTER ROLE rag_ref LOGIN PASSWORD %L', :'password') \gexec
CREATE SCHEMA IF NOT EXISTS rag_ref AUTHORIZATION rag_ref;
GRANT CONNECT ON DATABASE app TO rag_ref;
SQL
echo "postgres: role rag_ref and schema rag_ref ready"
