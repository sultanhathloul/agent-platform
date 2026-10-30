-- Owner decision on PR #17 (ADR-0410): which version of its kind's extractor indexed a KB document, so the seed
-- re-indexes, once, what an older extractor cut (a workbook's chunks now repeat their table's title and
-- header rows). Every document indexed before this migration was cut by version 1.
ALTER TABLE agent.kb_documents ADD COLUMN extractor text NOT NULL DEFAULT '1';
