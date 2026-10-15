-- mcp-rag-ref (phase 05, I10, ADR-0211): the reference RAG server's own documents and chunks, in a
-- schema of its own, so its rows and the agent's internal backend's never mix. A session document
-- is one user's; a knowledge base document (collection kb_{slug}) is its tenant's, stored with an
-- empty user id.
CREATE TABLE rag_ref.documents (
    id          uuid        PRIMARY KEY,
    tenant_id   text        NOT NULL,
    user_id     text        NOT NULL,
    collection  text        NOT NULL,
    name        text        NOT NULL,
    mime        text        NOT NULL,
    kind        text        NOT NULL CHECK (kind IN ('pdf', 'docx', 'xlsx', 'txt', 'md')),
    size        bigint      NOT NULL CHECK (size >= 0),
    object_key  text        NOT NULL,
    status      text        NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'indexed', 'failed')),
    reason      text,
    pages       integer,
    warnings    jsonb       NOT NULL DEFAULT '[]'::jsonb,
    attempts    integer     NOT NULL DEFAULT 0,
    created_at  timestamptz NOT NULL DEFAULT now(),
    indexed_at  timestamptz
);

CREATE INDEX documents_scope_idx ON rag_ref.documents (collection, tenant_id, user_id);
CREATE INDEX documents_status_idx ON rag_ref.documents (status);

CREATE TABLE rag_ref.chunks (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    collection    text        NOT NULL,
    attachment_id uuid        NOT NULL REFERENCES rag_ref.documents (id) ON DELETE CASCADE,
    user_id       text        NOT NULL,
    tenant_id     text        NOT NULL,
    page          integer,
    section       text,
    ord           integer     NOT NULL,
    text          text        NOT NULL,
    text_norm     text        NOT NULL,
    tsv           tsvector    GENERATED ALWAYS AS (to_tsvector('simple', text_norm)) STORED,
    embedding     vector      NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX chunks_scope_idx ON rag_ref.chunks (collection, tenant_id, user_id);
CREATE INDEX chunks_document_idx ON rag_ref.chunks (attachment_id);
CREATE INDEX chunks_tsv_idx ON rag_ref.chunks USING gin (tsv);
