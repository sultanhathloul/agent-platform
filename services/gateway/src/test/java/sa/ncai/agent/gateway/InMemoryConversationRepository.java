package sa.ncai.agent.gateway;

import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import sa.ncai.agent.gateway.chat.ConversationRepository;

public class InMemoryConversationRepository implements ConversationRepository {

    private final Map<String, ConversationOwner> owners = new ConcurrentHashMap<>();

    @Override
    public ConversationOwner bindOrGetOwner(String conversationId, String userId, String tenantId) {
        return owners.computeIfAbsent(
                conversationId.toLowerCase(Locale.ROOT), id -> new ConversationOwner(userId, tenantId));
    }

    public void clear() {
        owners.clear();
    }
}
