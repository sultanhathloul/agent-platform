-- Conversation ownership (ADR-0041): an id is bound to the user and tenant of its first turn.
CREATE TABLE gateway.conversations (
    id         uuid        PRIMARY KEY,
    user_id    text        NOT NULL,
    tenant_id  text        NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX conversations_owner_idx ON gateway.conversations (tenant_id, user_id, created_at);
