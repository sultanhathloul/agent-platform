package sa.ncai.agent.gateway.proxy;

/**
 * Body of the agent's {@code POST /v1/conversations/{id}/turns/{turn_id}/retract} (ADR-0048).
 *
 * @param principal the principal the turn ran with; the agent checks it owns the conversation
 * @param reason    error code that withdrew the reply, e.g. {@code audit_unavailable}
 */
public record AgentRetractRequest(AgentTurnRequest.Principal principal, String reason) {}
