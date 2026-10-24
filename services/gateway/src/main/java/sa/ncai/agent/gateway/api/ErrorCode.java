package sa.ncai.agent.gateway.api;

/** Stable error codes; messages live in messages_ar/en.properties under {@code error.<code>}. */
public enum ErrorCode {
    UNAUTHORIZED("unauthorized", 401),
    FORBIDDEN("forbidden", 403),
    KB_FORBIDDEN("kb_forbidden", 403),
    CONVERSATION_FORBIDDEN("conversation_forbidden", 403),
    /** A picked skill the caller holds no skill:{slug} role for, or the agent finds unavailable (phase 06). */
    SKILL_FORBIDDEN("skill_forbidden", 403),
    INVALID_REQUEST("invalid_request", 400),
    NOT_FOUND("not_found", 404),
    MEMORY_NOT_FOUND("memory_not_found", 404),
    ATTACHMENT_NOT_FOUND("attachment_not_found", 404),
    ATTACHMENT_LIMIT("attachment_limit", 409),
    FILE_TOO_LARGE("file_too_large", 413),
    TOO_MANY_PAGES("too_many_pages", 413),
    FILE_TYPE_UNSUPPORTED("file_type_unsupported", 415),
    UPLOAD_RATE_LIMITED("upload_rate_limited", 429),
    TOO_MANY_UPLOADS("too_many_uploads", 429),
    UPLOAD_TIMEOUT("upload_timeout", 408),
    STORAGE_UNAVAILABLE("storage_unavailable", 502),
    POLICY_NOT_FOUND("policy_not_found", 404),
    POLICY_EXISTS("policy_exists", 409),
    POLICY_INVALID("policy_invalid", 400),
    /** A platform settings save the timeout budget's rules or ranges refuse (ADR-0278). */
    SETTINGS_INVALID("settings_invalid", 400),
    /** Another administrator saved the settings first (ADR-0276). */
    SETTINGS_CONFLICT("settings_conflict", 409),
    SETTINGS_VERSION_NOT_FOUND("settings_version_not_found", 404),
    TURN_IN_FLIGHT("turn_in_flight", 409),
    /** The caller's or its tenant's turns in flight at their limit (config/turn.yaml; ADR-0262). */
    TOO_MANY_TURNS("too_many_turns", 429),
    AGENT_UNAVAILABLE("agent_unavailable", 502),
    AGENT_STREAM_INTERRUPTED("agent_stream_interrupted", 502),
    /** A turn past its whole-turn bound (config/turn.yaml; I2, I3), from the agent or the relay. */
    TURN_TIMEOUT("turn_timeout", 504),
    IDENTITY_UNAVAILABLE("identity_unavailable", 502),
    AUDIT_UNAVAILABLE("audit_unavailable", 503),
    SERVICE_BUSY("service_busy", 503),
    INTERNAL_ERROR("internal_error", 500);

    private final String code;
    private final int status;

    ErrorCode(String code, int status) {
        this.code = code;
        this.status = status;
    }

    public String code() {
        return code;
    }

    public int status() {
        return status;
    }
}
