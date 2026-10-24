-- Platform settings (owner decision 2026-10-22, ADR-0276). The capacity and timeout values of
-- config/turn.yaml, config/models.yaml, config/docs.yaml and config/mcp.yaml stay the defaults;
-- an administrator's overrides are versions of one document here, like the guard policies
-- (ADR-0166 pattern): a save, a revert and a restore each insert the next version, and the
-- newest version is in force. No row is ever updated or deleted: a trigger refuses both (the test
-- suite empties the table with TRUNCATE at the start of a session, as it does every agent table).
-- `content` holds the overrides only (key: value), never a default. Who made each version is kept
-- with the username and display name the verified token carried (ADR-0123); the seed names itself.
CREATE TABLE agent.platform_settings (
    version             integer     PRIMARY KEY CHECK (version >= 1),
    content             jsonb       NOT NULL,
    change              text        NOT NULL CHECK (change IN ('seed', 'save', 'revert', 'restore')),
    -- The version a revert brought back; null for every other change.
    reverted_to         integer     REFERENCES agent.platform_settings (version),
    created_at          timestamptz NOT NULL DEFAULT now(),
    created_by          text        NOT NULL,
    created_by_username text,
    created_by_name     text,
    CHECK ((change = 'revert') = (reverted_to IS NOT NULL))
);

-- One line: the migration runner splits statements at a `;` that ends a line (app/db/__init__.py).
CREATE FUNCTION agent.platform_settings_append_only() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'agent.platform_settings is append-only (ADR-0276)'; END; $$;

CREATE TRIGGER platform_settings_append_only
    BEFORE UPDATE OR DELETE ON agent.platform_settings
    FOR EACH ROW EXECUTE FUNCTION agent.platform_settings_append_only();
