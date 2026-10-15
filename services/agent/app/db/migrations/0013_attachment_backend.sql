-- I11 (ADR-0212): the document backend an upload was registered with. Its status is always read
-- from that backend, and the ingest service indexes only the internal backend's uploads.
ALTER TABLE agent.attachments ADD COLUMN backend text NOT NULL DEFAULT 'internal';
