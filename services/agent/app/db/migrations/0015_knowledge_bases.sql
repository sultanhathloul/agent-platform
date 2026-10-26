-- Knowledge bases (phase 08, ADR-0333 to ADR-0339). A knowledge base belongs to the tenant of the
-- administrator who created it (ADR-0202: a knowledge base collection is its tenant's); its slug is
-- unique on the platform, because its realm role `kb:<slug>:read` is (ADR-0006). Deleting one is
-- soft (I1): the row stays, hidden, with who deleted it and when; its slug and role stay taken.
CREATE TABLE agent.kbs (
    slug               text        PRIMARY KEY CHECK (slug ~ '^[a-z][a-z0-9_-]{0,62}$'),
    tenant_id          text        NOT NULL,
    name_ar            text        NOT NULL CHECK (length(name_ar) BETWEEN 1 AND 120),
    name_en            text        NOT NULL CHECK (length(name_en) BETWEEN 1 AND 120),
    description_ar     text        NOT NULL DEFAULT '' CHECK (length(description_ar) <= 500),
    description_en     text        NOT NULL DEFAULT '' CHECK (length(description_en) <= 500),
    icon               text        NOT NULL DEFAULT 'library' CHECK (icon ~ '^[a-z][a-z0-9-]{0,39}$'),
    created_at         timestamptz NOT NULL DEFAULT now(),
    created_by         text        NOT NULL,
    created_by_username text,
    created_by_name    text,
    deleted_at         timestamptz,
    deleted_by         text,
    deleted_by_username text,
    deleted_by_name    text,
    -- When a deleted KB's chunks left the index (I1); null while that purge is still owed (a backend
    -- that could not answer), retried when the agent starts.
    index_purged_at    timestamptz,
    CHECK ((deleted_at IS NULL) = (deleted_by IS NULL))
);

CREATE INDEX kbs_tenant_idx ON agent.kbs (tenant_id) WHERE deleted_at IS NULL;

-- A KB document (I2, D3): the stored file, its ingestion status as for an attachment, and the
-- source metadata an administrator enters: title, publisher, an optional source URL, the
-- publication date and an optional earlier version of the same source in the same KB it
-- supersedes. No row is deleted by the product (ADR-0166); a deleted KB's chunks are purged from
-- the index and its documents marked purged.
CREATE TABLE agent.kb_documents (
    id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    kb_slug          text        NOT NULL REFERENCES agent.kbs (slug),
    tenant_id        text        NOT NULL,
    name             text        NOT NULL,
    mime             text        NOT NULL,
    kind             text        NOT NULL CHECK (kind IN ('pdf', 'docx', 'xlsx', 'txt', 'md')),
    size             bigint      NOT NULL CHECK (size >= 0),
    object_key       text        NOT NULL,
    collection       text        NOT NULL,
    backend          text        NOT NULL DEFAULT 'internal',
    status           text        NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'indexed', 'failed')),
    reason           text,
    pages            integer,
    chunks           integer,
    warnings         jsonb       NOT NULL DEFAULT '[]'::jsonb,
    attempts         integer     NOT NULL DEFAULT 0,
    title            text        NOT NULL CHECK (length(title) BETWEEN 1 AND 300),
    publisher        text        NOT NULL CHECK (length(publisher) BETWEEN 1 AND 200),
    source_url       text        CHECK (source_url IS NULL OR source_url ~ '^https?://'),
    published_on     date        NOT NULL,
    supersedes       uuid        REFERENCES agent.kb_documents (id),
    uploaded_by      text        NOT NULL,
    uploaded_by_username text,
    uploaded_by_name text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    indexed_at       timestamptz,
    purged_at        timestamptz,
    CHECK (supersedes IS DISTINCT FROM id)
);

CREATE INDEX kb_documents_kb_idx ON agent.kb_documents (kb_slug, created_at);
CREATE INDEX kb_documents_status_idx ON agent.kb_documents (status);
CREATE INDEX kb_documents_object_key_idx ON agent.kb_documents (object_key);
-- At most one document supersedes a given one: a source's versions form a chain, never a fork.
CREATE UNIQUE INDEX kb_documents_supersedes_idx ON agent.kb_documents (supersedes) WHERE supersedes IS NOT NULL;

-- A chunk belongs to an attachment or to a KB document, never both (BACKLOG, phase 08 item 2):
-- deleting either record deletes its chunks.
ALTER TABLE agent.rag_chunks ALTER COLUMN attachment_id DROP NOT NULL;
ALTER TABLE agent.rag_chunks ADD COLUMN kb_document_id uuid REFERENCES agent.kb_documents (id) ON DELETE CASCADE;
ALTER TABLE agent.rag_chunks ADD CONSTRAINT rag_chunks_one_document CHECK (num_nonnulls(attachment_id, kb_document_id) = 1);
CREATE INDEX rag_chunks_kb_document_idx ON agent.rag_chunks (kb_document_id);
