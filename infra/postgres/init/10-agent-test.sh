#!/usr/bin/env bash
# The agent's integration-test database (ADR-0070): owned by the `app` role, with
# the vector extension, so the tests connect as the app role like the service
# does, never as the superuser. Runs on an empty data directory after
# 00-databases.sh, and `make test-agent` re-runs it on an existing volume, so it
# is idempotent.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER:-postgres}" --dbname postgres <<'SQL'
SELECT 'CREATE DATABASE agent_test OWNER app'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'agent_test') \gexec
-- Only its owner connects (ADR-0242); infra/postgres/roles/connect.sh keeps it so.
REVOKE CONNECT ON DATABASE agent_test FROM PUBLIC;
GRANT CONNECT ON DATABASE agent_test TO app;
SQL
psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER:-postgres}" --dbname agent_test <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
SQL
