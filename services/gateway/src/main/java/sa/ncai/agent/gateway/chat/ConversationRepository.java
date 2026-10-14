package sa.ncai.agent.gateway.chat;

/** Conversation ownership (ADR-0041): a conversation id belongs to the user and tenant that used it first. */
public interface ConversationRepository {

    /** Binds {@code conversationId} to the caller when it is new, and returns its owner either way. */
    ConversationOwner bindOrGetOwner(String conversationId, String userId, String tenantId);

    /** The owner of {@code conversationId}, without binding anyone; empty for a conversation nobody has used. */
    java.util.Optional<ConversationOwner> ownerOf(String conversationId);

    record ConversationOwner(String userId, String tenantId) {

        public boolean is(String userId, String tenantId) {
            return this.userId.equals(userId) && this.tenantId.equals(tenantId);
        }
    }
}
