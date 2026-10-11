-- Owner item (UI fix session, ADR-0119). A policy carries its name and description in Arabic and
-- English. A stored version's `name` and `description` become `name_en` and `description_en`.
-- Both names are required, and no Arabic name exists for a version saved before this, so its
-- `name_ar` starts as a copy of the English one for an admin to replace; `description_ar` starts
-- empty and the page shows the English description in its place. This renames fields of stored
-- versions; it changes no policy's behaviour, so no version is added.
UPDATE agent.guard_policies
SET content = (content - 'name' - 'description')
    || jsonb_build_object(
        'name_en', content->'name',
        'name_ar', content->'name',
        'description_en', coalesce(content->'description', '""'::jsonb),
        'description_ar', ''
    )
WHERE content ? 'name';
