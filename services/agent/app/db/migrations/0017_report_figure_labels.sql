-- Owner decision A on the report branch (ADR-0388): a figure carries its source's label, not an
-- "unverified" mark. A version keeps each sourced figure's label for a follow-up (D4), and counts the
-- figures labelled general knowledge.
ALTER TABLE agent.report_versions RENAME COLUMN verified TO figure_labels;
ALTER TABLE agent.report_versions ALTER COLUMN figure_labels SET DEFAULT '{}'::jsonb;
UPDATE agent.report_versions SET figure_labels = '{}'::jsonb WHERE jsonb_typeof(figure_labels) <> 'object';
ALTER TABLE agent.report_versions RENAME COLUMN unverified TO general;
ALTER TABLE agent.report_versions RENAME CONSTRAINT report_versions_unverified_check TO report_versions_general_check;
