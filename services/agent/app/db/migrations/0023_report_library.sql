-- The personal report library (report library PR, owner decision 3, ADR-0432). One row per report version,
-- written in the same transaction as the version (and by a bounded backfill for versions written before this
-- migration): what a search reads, never the page. `text_norm` is the version's title, subject, section
-- titles, KPI labels and values, table headers and source names, Arabic-normalized by the agent
-- (app/text/arabic.py), as rag_chunks.text_norm; `tsv` is generated from it. Every read names the owner.
CREATE TABLE agent.report_library (
    report_id       uuid        NOT NULL,
    version         integer     NOT NULL CHECK (version >= 1),
    tenant_id       text        NOT NULL,
    user_id         text        NOT NULL,
    conversation_id text        NOT NULL,
    title           text        NOT NULL CHECK (length(title) BETWEEN 1 AND 300),
    subject         text        NOT NULL DEFAULT '' CHECK (length(subject) <= 400),
    sections        jsonb       NOT NULL DEFAULT '[]'::jsonb,
    -- The source names a version cites (KB names, file titles), and its kind: kb, file or general.
    sources         jsonb       NOT NULL DEFAULT '[]'::jsonb,
    source_kinds    text[]      NOT NULL DEFAULT '{}',
    text_norm       text        NOT NULL CHECK (length(text_norm) <= 20000),
    tsv             tsvector    GENERATED ALWAYS AS (to_tsvector('simple', text_norm)) STORED,
    created_at      timestamptz NOT NULL,
    indexed_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (report_id, version),
    FOREIGN KEY (report_id, version) REFERENCES agent.report_versions (report_id, version)
);

CREATE INDEX report_library_owner_idx ON agent.report_library (tenant_id, user_id, created_at DESC);
CREATE INDEX report_library_tsv_idx ON agent.report_library USING gin (tsv);

-- A report made in one conversation from another conversation's report (owner decision 3): the version it
-- was based on. The original is never changed.
ALTER TABLE agent.reports ADD COLUMN based_on_report uuid;
ALTER TABLE agent.reports ADD COLUMN based_on_version integer;

-- A conversation that read a report of the caller's library (report_read or report_search through the recall
-- capability): the next report turn may make a new version of it in this conversation (ADR-0433). Owner and
-- conversation on every row; at most a few per conversation are read (the latest first).
CREATE TABLE agent.report_references (
    conversation_id text        NOT NULL,
    tenant_id       text        NOT NULL,
    user_id         text        NOT NULL,
    report_id       uuid        NOT NULL REFERENCES agent.reports (id),
    version         integer     NOT NULL CHECK (version >= 1),
    referenced_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (conversation_id, report_id)
);

CREATE INDEX report_references_conversation_idx
    ON agent.report_references (conversation_id, tenant_id, user_id, referenced_at DESC);
