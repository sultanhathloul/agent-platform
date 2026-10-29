-- Owner decision 1 on PR #17 (ADR-0402): a version keeps the passages behind its citations, by reference
-- (backend, collection, ref and their metadata, never their text), so an edit with the knowledge base still
-- toggled can fetch them again from the knowledge base, under that turn's permissions.
ALTER TABLE agent.report_versions ADD COLUMN passages jsonb NOT NULL DEFAULT '[]'::jsonb;
