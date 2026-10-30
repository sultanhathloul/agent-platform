-- Review round 1 of PR #17, finding 9 (ADR-0424): a re-index runs beside the document's indexed chunks. The
-- document stays `indexed` (in retrieval, on its old chunks) while `reindex` is 'pending'; the new chunks
-- replace the old in one transaction when they are indexed; a re-index that fails keeps the old chunks and is
-- 'failed', with its reason, for administrators to retry.
ALTER TABLE agent.kb_documents ADD COLUMN reindex text CHECK (reindex IN ('pending', 'failed'));
ALTER TABLE agent.kb_documents ADD COLUMN reindex_reason text;
