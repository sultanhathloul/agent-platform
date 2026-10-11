-- Owner item (UI session 2, ADR-0123). A change row and a version row keep the actor's username
-- and display name as they were at the time of the change, beside the id, so the admin history
-- names a person rather than a Keycloak subject. The gateway supplies both from the verified
-- token. Rows written by the seed or the backfill (0006) name themselves: `seed` / `backfill`
-- in all three fields. Other rows saved before this keep no name; the page shows their id.
ALTER TABLE agent.guard_policies ADD COLUMN IF NOT EXISTS created_by_username text;

ALTER TABLE agent.guard_policies ADD COLUMN IF NOT EXISTS created_by_name text;

ALTER TABLE agent.guard_policy_changes ADD COLUMN IF NOT EXISTS actor_username text;

ALTER TABLE agent.guard_policy_changes ADD COLUMN IF NOT EXISTS actor_name text;

UPDATE agent.guard_policies
SET created_by_username = created_by, created_by_name = created_by
WHERE created_by IN ('seed', 'backfill') AND created_by_name IS NULL;

UPDATE agent.guard_policy_changes
SET actor_username = actor, actor_name = actor
WHERE actor IN ('seed', 'backfill') AND actor_name IS NULL;
