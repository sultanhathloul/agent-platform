-- Option C (owner decision A on PR #17, ADR-0406): the writer answers with a report spec and the platform
-- renders it; each version keeps its checked spec, which an edit changes. A version written before it has
-- none (NULL).
ALTER TABLE agent.report_versions ADD COLUMN spec jsonb;
