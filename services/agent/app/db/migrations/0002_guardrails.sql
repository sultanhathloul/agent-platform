-- Phase 03. Guard policies are owned by the agent; the gateway only proxies admin calls
-- (I1, ADR-0059 pattern). A version is immutable: an edit inserts a new row (I2), and the
-- repository never updates or deletes one. Whether a policy is on, and which version is
-- current, lives in its head row, so switching a policy on or off never makes a version.
CREATE TABLE agent.guard_policies (
    id         text        NOT NULL,
    version    integer     NOT NULL CHECK (version >= 1),
    content    jsonb       NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by text        NOT NULL,
    PRIMARY KEY (id, version)
);

CREATE TABLE agent.guard_policy_heads (
    id              text        PRIMARY KEY,
    current_version integer     NOT NULL,
    enabled         boolean     NOT NULL,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    updated_by      text        NOT NULL,
    FOREIGN KEY (id, current_version) REFERENCES agent.guard_policies (id, version)
);

-- Matched policy evaluations of real turns, for the admin's matches per day (README §10.4).
-- No content: which policy, which version, what it did, where (D5).
CREATE TABLE agent.guard_events (
    id              bigserial   PRIMARY KEY,
    created_at      timestamptz NOT NULL DEFAULT now(),
    tenant_id       text        NOT NULL,
    conversation_id uuid,
    turn_id         text,
    checkpoint      text        NOT NULL,
    policy_id       text        NOT NULL,
    policy_version  integer     NOT NULL,
    action          text        NOT NULL,
    target          text
);

CREATE INDEX guard_events_policy_day_idx ON agent.guard_events (policy_id, created_at);

-- Retention of recorded context packages (D5): they expire, and they name the memory items
-- they used, so deleting an item deletes the packages that hold its text.
ALTER TABLE agent.context_packages ADD COLUMN expires_at timestamptz;

ALTER TABLE agent.context_packages ADD COLUMN memory_item_ids uuid[] NOT NULL DEFAULT '{}';

CREATE INDEX context_packages_expiry_idx ON agent.context_packages (expires_at);

CREATE INDEX context_packages_memory_idx ON agent.context_packages USING gin (memory_item_ids);
