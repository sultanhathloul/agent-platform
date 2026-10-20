#!/usr/bin/env bash
# Runs once, on an empty data directory. Creates one database per consumer,
# each owned by its own login role, and the extensions the app database needs.
set -euo pipefail

create_db() {
  local db="$1" owner="$2" password="$3"
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
    -v db="$db" -v owner="$owner" -v password="$password" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'owner', :'password') \gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'db', :'owner') \gexec
SELECT format('REVOKE CONNECT ON DATABASE %I FROM PUBLIC', :'db') WHERE :'db' <> 'app' \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', :'db', :'owner') \gexec
SQL
}

create_db app app "$APP_DB_PASSWORD"
create_db business business "$BUSINESS_DB_PASSWORD"
create_db keycloak keycloak "$KEYCLOAK_DB_PASSWORD"
create_db langfuse langfuse "$LANGFUSE_DB_PASSWORD"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname app <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
SQL
