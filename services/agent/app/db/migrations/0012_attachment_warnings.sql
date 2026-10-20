-- D7 (ADR-0201): an indexed file may carry warnings, [{code: pages_without_text, count}] for a
-- partly scanned PDF whose pages under extraction.min_text_chars_per_page were not read.
ALTER TABLE agent.attachments ADD COLUMN warnings jsonb NOT NULL DEFAULT '[]'::jsonb;
