-- Seeded knowledge bases (owner decision B on the report branch, ADR-0392): the SHA-256 of a KB document's
-- file, computed by the gateway as it stores the upload, so the seed step skips a document already in the
-- KB; and whether its source metadata was missing when it was uploaded (a file dropped into kb-seed/
-- without a sidecar), shown on its admin page until an administrator corrects the metadata.
ALTER TABLE agent.kb_documents ADD COLUMN sha256 text CHECK (sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$');
ALTER TABLE agent.kb_documents ADD COLUMN metadata_missing boolean NOT NULL DEFAULT false;
CREATE INDEX kb_documents_sha256_idx ON agent.kb_documents (kb_slug, sha256);
