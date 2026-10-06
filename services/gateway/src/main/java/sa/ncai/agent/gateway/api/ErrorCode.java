package sa.ncai.agent.gateway.api;

/** Stable error codes; messages live in messages_ar/en.properties under {@code error.<code>}. */
public enum ErrorCode {
    UNAUTHORIZED("unauthorized", 401),
    FORBIDDEN("forbidden", 403),
    KB_FORBIDDEN("kb_forbidden", 403),
    CONVERSATION_FORBIDDEN("conversation_forbidden", 403),
    INVALID_REQUEST("invalid_request", 400),
    NOT_FOUND("not_found", 404),
    MEMORY_NOT_FOUND("memory_not_found", 404),
    POLICY_NOT_FOUND("policy_not_found", 404),
    POLICY_EXISTS("policy_exists", 409),
    POLICY_INVALID("policy_invalid", 400),
    TURN_IN_FLIGHT("turn_in_flight", 409),
    AGENT_UNAVAILABLE("agent_unavailable", 502),
    AGENT_STREAM_INTERRUPTED("agent_stream_interrupted", 502),
    AUDIT_UNAVAILABLE("audit_unavailable", 503),
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
