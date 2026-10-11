-- Phase 04 (ADR-0125, ADR-0130, ADR-0131, ADR-0132). Attachments are owned by the agent;
-- the gateway stores the bytes in MinIO and registers them here (ADR-0129).

CREATE TABLE agent.attachments (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id uuid        NOT NULL,
    tenant_id       text        NOT NULL,
    user_id         text        NOT NULL,
    name            text        NOT NULL,
    mime            text        NOT NULL,
    kind            text        NOT NULL CHECK (kind IN ('pdf', 'docx', 'xlsx', 'txt', 'md')),
    size            bigint      NOT NULL CHECK (size >= 0),
    object_key      text        NOT NULL,
    collection      text        NOT NULL,
    status          text        NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'indexed', 'failed')),
    reason          text,
    pages           integer,
    chunks          integer,
    attempts        integer     NOT NULL DEFAULT 0,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    indexed_at      timestamptz,
    expires_at      timestamptz NOT NULL
);

CREATE INDEX attachments_owner_idx ON agent.attachments (tenant_id, user_id, conversation_id, created_at);
CREATE INDEX attachments_status_idx ON agent.attachments (status);
CREATE INDEX attachments_expiry_idx ON agent.attachments (expires_at);

-- One chunk table for every collection (ADR-0131): sess_{conversation_id} now, kb_{slug}
-- in phase 05. `embedding` has no fixed dimension (ADR-0058's reasoning); `tsv` is the
-- `simple` configuration over Arabic-normalized text (ADR-0132).
CREATE TABLE agent.rag_chunks (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    collection    text        NOT NULL,
    attachment_id uuid        NOT NULL,
    user_id       text        NOT NULL,
    tenant_id     text        NOT NULL,
    page          integer,
    section       text,
    ord           integer     NOT NULL,
    text          text        NOT NULL,
    text_norm     text        NOT NULL,
    tsv           tsvector    GENERATED ALWAYS AS (to_tsvector('simple', text_norm)) STORED,
    embedding     vector      NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz
);

CREATE INDEX rag_chunks_scope_idx ON agent.rag_chunks (collection, tenant_id, user_id);
CREATE INDEX rag_chunks_attachment_idx ON agent.rag_chunks (attachment_id);
CREATE INDEX rag_chunks_tsv_idx ON agent.rag_chunks USING gin (tsv);
CREATE INDEX rag_chunks_expiry_idx ON agent.rag_chunks (expires_at);
