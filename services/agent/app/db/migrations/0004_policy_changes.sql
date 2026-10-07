-- Owner decision (ADR-0108). What a policy overrides, and whether that makes it an exception,
-- describes the current versions of its group, so it lives on the head and is recomputed for
-- the whole group in the transaction of every save, import, seed and enable. The columns on
-- agent.guard_policies stay as they were at save time.
ALTER TABLE agent.guard_policy_heads ADD COLUMN overrides jsonb NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE agent.guard_policy_heads ADD COLUMN exception boolean NOT NULL DEFAULT false;

-- The audit of truth for policy changes, written by the agent in the change's own transaction;
-- the gateway's audit record stays as it is (ADR-0059 pattern). Never updated or deleted.
CREATE TABLE agent.guard_policy_changes (
    id         bigserial   PRIMARY KEY,
    changed_at timestamptz NOT NULL DEFAULT now(),
    policy_id  text        NOT NULL,
    version    integer     NOT NULL,
    change     text        NOT NULL CHECK (change IN ('create', 'version', 'enable', 'disable', 'import', 'seed')),
    actor      text        NOT NULL,
    exception  boolean     NOT NULL,
    overrides  jsonb       NOT NULL,
    FOREIGN KEY (policy_id, version) REFERENCES agent.guard_policies (id, version)
);

CREATE INDEX guard_policy_changes_policy_idx ON agent.guard_policy_changes (policy_id, changed_at DESC);
