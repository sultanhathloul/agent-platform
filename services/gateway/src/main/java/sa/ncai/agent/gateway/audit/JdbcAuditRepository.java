package sa.ncai.agent.gateway.audit;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

@Repository
public class JdbcAuditRepository implements AuditRepository {

    private final JdbcClient jdbc;
    private final ObjectMapper mapper;

    public JdbcAuditRepository(JdbcClient jdbc, ObjectMapper mapper) {
        this.jdbc = jdbc;
        this.mapper = mapper;
    }

    @Override
    public void append(AuditEvent event) {
        jdbc.sql("""
                INSERT INTO gateway.audit_events
                    (request_id, user_id, tenant_id, action, conversation_id, status, detail)
                VALUES
                    (:requestId, :userId, :tenantId, :action, :conversationId, :status, CAST(:detail AS jsonb))
                """)
                .param("requestId", event.requestId())
                .param("userId", event.userId())
                .param("tenantId", event.tenantId())
                .param("action", event.action())
                .param("conversationId", event.conversationId())
                .param("status", event.status().dbValue())
                .param("detail", toJson(event))
                .update();
    }

    private String toJson(AuditEvent event) {
        try {
            return mapper.writeValueAsString(event.detail());
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("audit detail is not serializable", e);
        }
    }
}
