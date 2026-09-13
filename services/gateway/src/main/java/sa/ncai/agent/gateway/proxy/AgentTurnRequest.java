package sa.ncai.agent.gateway.proxy;

import java.util.List;
import sa.ncai.agent.gateway.chat.Attachment;

/** Body of the agent's {@code POST /v1/turns} (README §6.2). */
public record AgentTurnRequest(
        String conversationId,
        Principal principal,
        String message,
        List<Attachment> attachments,
        List<String> selectedKbs,
        String locale) {

    /** Identity set by the gateway, never by the model. {@code roles} only select tier ceilings (ADR-0040). */
    public record Principal(
            String userId,
            String tenantId,
            List<String> permissions,
            List<String> roles,
            String displayName,
            String locale) {}
}
