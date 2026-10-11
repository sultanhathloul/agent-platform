-- Owner decision (ADR-0115). A change that turns other policies into exceptions, or clears their
-- flag, names them on its own row (`induced`: [{policy: id@version, exception}]), and each of them
-- gets a row of its own, `flagged` or `unflagged`, with the acting change's actor.
ALTER TABLE agent.guard_policy_changes ADD COLUMN induced jsonb NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE agent.guard_policy_changes DROP CONSTRAINT guard_policy_changes_change_check;

ALTER TABLE agent.guard_policy_changes ADD CONSTRAINT guard_policy_changes_change_check CHECK (
    change IN ('create', 'version', 'enable', 'disable', 'import', 'seed', 'flagged', 'unflagged')
);
