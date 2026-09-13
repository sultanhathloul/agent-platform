-- Append-only audit log (README §6.1.4, §16): one row per chat turn.
CREATE TABLE gateway.audit_events (
    id              bigserial   PRIMARY KEY,
    occurred_at     timestamptz NOT NULL DEFAULT now(),
    request_id      text        NOT NULL,
    user_id         text        NOT NULL,
    tenant_id       text        NOT NULL,
    action          text        NOT NULL,
    conversation_id text,
    status          text        NOT NULL CHECK (status IN ('ok', 'error', 'denied', 'aborted')),
    detail          jsonb       NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX audit_events_user_time_idx ON gateway.audit_events (user_id, occurred_at);
CREATE INDEX audit_events_request_idx ON gateway.audit_events (request_id);

CREATE FUNCTION gateway.audit_events_append_only() RETURNS trigger
    LANGUAGE plpgsql AS
$$
BEGIN
    RAISE EXCEPTION 'gateway.audit_events is append-only: % is not allowed', TG_OP;
END;
$$;

CREATE TRIGGER audit_events_no_update_delete
    BEFORE UPDATE OR DELETE ON gateway.audit_events
    FOR EACH ROW EXECUTE FUNCTION gateway.audit_events_append_only();

CREATE TRIGGER audit_events_no_truncate
    BEFORE TRUNCATE ON gateway.audit_events
    FOR EACH STATEMENT EXECUTE FUNCTION gateway.audit_events_append_only();
