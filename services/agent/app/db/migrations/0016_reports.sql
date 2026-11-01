-- Interactive reports (reports PR, I5, ADR-0373). A report belongs to the owner of the conversation it
-- was written in; only that owner reads it (checked at the gateway and the agent). Each version's page is
-- one object in MinIO (bucket `artifacts`, key <tenant>/<conversation>/<report>/v<n>.html); the row keeps
-- what a reader and a follow-up need without the page: its size and hash, the citations of its sources
-- section, the figures it verified (a follow-up's check counts them as sourced, D4) and how many it marked.
-- No row is deleted by the product (ADR-0166).
CREATE TABLE agent.reports (
    id              uuid        PRIMARY KEY,
    tenant_id       text        NOT NULL,
    user_id         text        NOT NULL,
    conversation_id text        NOT NULL,
    title           text        NOT NULL CHECK (length(title) BETWEEN 1 AND 300),
    latest_version  integer     NOT NULL DEFAULT 0 CHECK (latest_version >= 0),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX reports_conversation_idx ON agent.reports (conversation_id, tenant_id, user_id, updated_at);

CREATE TABLE agent.report_versions (
    report_id       uuid        NOT NULL REFERENCES agent.reports (id),
    version         integer     NOT NULL CHECK (version >= 1),
    title           text        NOT NULL CHECK (length(title) BETWEEN 1 AND 300),
    object_key      text        NOT NULL,
    size            integer     NOT NULL CHECK (size > 0),
    sha256          text        NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    locale          text        NOT NULL CHECK (locale IN ('ar', 'en')),
    turn_id         text        NOT NULL,
    citations       jsonb       NOT NULL DEFAULT '[]'::jsonb,
    verified        jsonb       NOT NULL DEFAULT '[]'::jsonb,
    figures         integer     NOT NULL DEFAULT 0 CHECK (figures >= 0),
    unverified      integer     NOT NULL DEFAULT 0 CHECK (unverified >= 0),
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (report_id, version)
);
