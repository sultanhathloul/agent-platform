-- Phase 02 (ADR-0055, ADR-0058, ADR-0059). The LangGraph checkpointer creates its own
-- tables in this schema (checkpoints, checkpoint_blobs, checkpoint_writes, checkpoint_migrations).

-- Conversation ownership (ADR-0050), durable beside the checkpointer.
CREATE TABLE agent.conversation_owners (
    conversation_id uuid        PRIMARY KEY,
    user_id         text        NOT NULL,
    tenant_id       text        NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- Long-term memory (README §8). Every read and write names (tenant_id, user_id).
-- `embedding` has no fixed dimension, so the embedding model can change without a
-- migration; similarity is computed only between vectors of equal dimension.
CREATE TABLE agent.memory_items (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   text        NOT NULL,
    user_id     text        NOT NULL,
    kind        text        NOT NULL,
    text        text        NOT NULL,
    embedding   vector      NOT NULL,
    confidence  real        NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    source_turn text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz
);

CREATE INDEX memory_items_owner_idx ON agent.memory_items (tenant_id, user_id, created_at DESC);

-- Per-user memory switch; no row means the default from config/memory.yaml.
CREATE TABLE agent.memory_settings (
    tenant_id  text        NOT NULL,
    user_id    text        NOT NULL,
    enabled    boolean     NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, user_id)
);

-- What each model call saw (README §7, GET /v1/debug/context/{turn_id}); written only
-- while DEBUG_CONTEXT_ENABLED is true.
CREATE TABLE agent.context_packages (
    id              bigserial   PRIMARY KEY,
    turn_id         text        NOT NULL,
    conversation_id uuid        NOT NULL,
    node            text        NOT NULL,
    package         jsonb       NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX context_packages_turn_idx ON agent.context_packages (turn_id, id);
