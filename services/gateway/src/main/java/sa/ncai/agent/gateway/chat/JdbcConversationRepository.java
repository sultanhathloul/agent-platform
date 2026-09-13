package sa.ncai.agent.gateway.chat;

import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

@Repository
public class JdbcConversationRepository implements ConversationRepository {

    private final JdbcClient jdbc;

    public JdbcConversationRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    public ConversationOwner bindOrGetOwner(String conversationId, String userId, String tenantId) {
        UUID id = UUID.fromString(conversationId);
        // Insert-if-absent, then read: concurrent first turns agree on a single owner.
        jdbc.sql("""
                INSERT INTO gateway.conversations (id, user_id, tenant_id)
                VALUES (:id, :userId, :tenantId)
                ON CONFLICT (id) DO NOTHING
                """)
                .param("id", id)
                .param("userId", userId)
                .param("tenantId", tenantId)
                .update();
        return jdbc.sql("SELECT user_id, tenant_id FROM gateway.conversations WHERE id = :id")
                .param("id", id)
                .query((rs, row) -> new ConversationOwner(rs.getString("user_id"), rs.getString("tenant_id")))
                .single();
    }
}
