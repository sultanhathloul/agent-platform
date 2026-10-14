-- Owner decision S4 (supersedes D1, ADR-0125): the product deletes no uploaded file. Files stay
-- for as long as the platform keeps them; deleting one is an operations task (PROGRESS, phase 04
-- fix session), and these keys make it clean: deleting an attachment record deletes its chunks,
-- and every recorded context package names the attachments whose chunks it holds.

ALTER TABLE agent.attachments DROP COLUMN expires_at;
ALTER TABLE agent.rag_chunks DROP COLUMN expires_at;

-- Chunks whose attachment is already gone (a delete that lost a race before this key existed).
DELETE FROM agent.rag_chunks c WHERE NOT EXISTS (SELECT 1 FROM agent.attachments a WHERE a.id = c.attachment_id);
ALTER TABLE agent.rag_chunks
    ADD CONSTRAINT rag_chunks_attachment_fk
    FOREIGN KEY (attachment_id) REFERENCES agent.attachments (id) ON DELETE CASCADE;

ALTER TABLE agent.context_packages ADD COLUMN attachment_ids uuid[] NOT NULL DEFAULT '{}';
CREATE INDEX context_packages_attachments_idx ON agent.context_packages USING gin (attachment_ids);

-- The orphan sweeper looks an object key up.
CREATE INDEX attachments_object_key_idx ON agent.attachments (object_key);
