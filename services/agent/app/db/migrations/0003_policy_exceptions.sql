-- Owner decision (ADR-0105): when a version is saved, the policies of its group it overrides
-- (wider scope, shared checkpoint) are recorded with it, as id@version, and a version that
-- overrides a stricter one is an exception. Versions saved before this keep the defaults.
ALTER TABLE agent.guard_policies ADD COLUMN overrides jsonb NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE agent.guard_policies ADD COLUMN exception boolean NOT NULL DEFAULT false;
