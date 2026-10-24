package sa.ncai.agent.gateway.proxy;

import sa.ncai.agent.gateway.api.ErrorCode;

/**
 * The agent refused a turn before any stream started: 403 conversation_forbidden (ownership is
 * checked before the lock), 409 turn_in_flight (D6), 429 too_many_turns (the caller's or its
 * tenant's turns at their limit, ADR-0262) or 503 service_busy (the platform at capacity, or
 * admission past its bound), ADR-0109.
 */
public class AgentRefusedException extends RuntimeException {

    private final ErrorCode code;

    public AgentRefusedException(ErrorCode code) {
        super("the agent refused the turn: " + code.code());
        this.code = code;
    }

    public ErrorCode code() {
        return code;
    }

    /** The refusal a status stands for, or null when the status is not one. */
    public static AgentRefusedException forStatus(int status) {
        return forStatus(status, null);
    }

    /**
     * The refusal a status and the agent's error code stand for: a 403 is skill_forbidden when the
     * agent says so (phase 06), conversation_forbidden otherwise.
     */
    public static AgentRefusedException forStatus(int status, String agentCode) {
        if (status == 403 && ErrorCode.SKILL_FORBIDDEN.code().equals(agentCode)) {
            return new AgentRefusedException(ErrorCode.SKILL_FORBIDDEN);
        }
        return switch (status) {
            case 403 -> new AgentRefusedException(ErrorCode.CONVERSATION_FORBIDDEN);
            case 409 -> new AgentRefusedException(ErrorCode.TURN_IN_FLIGHT);
            case 429 -> new AgentRefusedException(ErrorCode.TOO_MANY_TURNS);
            case 503 -> new AgentRefusedException(ErrorCode.SERVICE_BUSY);
            default -> null;
        };
    }
}
