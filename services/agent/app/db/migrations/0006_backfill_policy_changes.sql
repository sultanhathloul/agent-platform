-- Owner item (UI fix session, ADR-0119). Versions saved before the change log existed (0004) have
-- no row there, so the admin change log showed nothing for them. Each gets one row, `seed` by
-- `backfill`, dated when the version was saved, with the flags it was saved with. Versions that
-- already have a row are left alone, so the migration adds nothing on a clean database.
INSERT INTO agent.guard_policy_changes (changed_at, policy_id, version, change, actor, exception, overrides, induced)
SELECT p.created_at, p.id, p.version, 'seed', 'backfill', p.exception, p.overrides, '[]'::jsonb
FROM agent.guard_policies p
WHERE NOT EXISTS (
    SELECT 1 FROM agent.guard_policy_changes c WHERE c.policy_id = p.id AND c.version = p.version
)
ORDER BY p.id, p.version;
