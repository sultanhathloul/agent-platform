package sa.ncai.agent.gateway.audit;

import java.util.Map;

/** One append-only audit record; {@code detail} is stored as jsonb. */
public record AuditEvent(
        String requestId,
        String userId,
        String tenantId,
        String action,
        String conversationId,
        AuditStatus status,
        Map<String, Object> detail) {}
