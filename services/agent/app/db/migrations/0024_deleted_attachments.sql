-- Tombstones of the files a user deleted (my-files review, findings 1 and 2; ADR-0448). A delete writes one in
-- the same transaction as it removes the record. A context package, a history citation or the turn's evidence
-- that names a tombstoned file is dropped when written; a turn already in flight when the file was deleted may
-- still record a package, so one purge pass, once `purge_after` (the longest turn deadline after the delete) has
-- passed, removes every package that names the file. A tombstone holds only ids and times, never a name.
CREATE TABLE agent.deleted_attachments (
    id          uuid        PRIMARY KEY,
    tenant_id   text        NOT NULL,
    user_id     text        NOT NULL,
    deleted_at  timestamptz NOT NULL DEFAULT now(),
    purge_after timestamptz NOT NULL,
    purged_at   timestamptz
);
CREATE INDEX deleted_attachments_due_idx ON agent.deleted_attachments (purge_after) WHERE purged_at IS NULL;
