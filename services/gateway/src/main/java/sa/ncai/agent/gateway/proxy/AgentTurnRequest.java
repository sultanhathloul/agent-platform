package sa.ncai.agent.gateway.proxy;

import com.fasterxml.jackson.annotation.JsonInclude;
import java.util.List;

/** Body of the agent's {@code POST /v1/turns} (README §6.2); {@code skill}: the picked skill, phase 06. */
public record AgentTurnRequest(
        String conversationId,
        Principal principal,
        String message,
        List<String> attachmentIds,
        List<String> selectedKbs,
        String locale,
        @JsonInclude(JsonInclude.Include.NON_NULL) String skill) {

    /**
     * Identity set by the gateway, never by the model. {@code maxTier} is the model tier ceiling
     * derived from the caller's roles, null when no role caps it; roles themselves never leave
     * the gateway (ADR-0046), except the {@code policy:*} roles, which select role-scoped guard
     * policies and grant nothing (ADR-0107). Built from the verified token only.
     */
    public record Principal(
            String userId,
            String tenantId,
            List<String> permissions,
            String maxTier,
            String displayName,
            String locale,
            @JsonInclude(JsonInclude.Include.NON_EMPTY) List<String> policyRoles) {}
}
