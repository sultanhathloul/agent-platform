package sa.ncai.agent.gateway.chat;

/** Conversation ownership (ADR-0041): a conversation id belongs to the user and tenant that used it first. */
public interface ConversationRepository {

    /** Binds {@code conversationId} to the caller when it is new, and returns its owner either way. */
    ConversationOwner bindOrGetOwner(String conversationId, String userId, String tenantId);

    /** The owner of {@code conversationId}, without binding anyone; empty for a conversation nobody has used. */
    java.util.Optional<ConversationOwner> ownerOf(String conversationId);

    /**
     * Of {@code conversationIds}, those the caller owns (report library PR, ADR-0436): the library list's own
     * check, one query for a page. Each id must be a lowercase UUID.
     */
    default java.util.Set<String> ownedBy(java.util.Collection<String> conversationIds, String userId, String tenantId) {
        java.util.Set<String> owned = new java.util.HashSet<>();
        for (String id : conversationIds) {
            ownerOf(id).filter(o -> o.is(userId, tenantId)).ifPresent(o -> owned.add(id));
        }
        return owned;
    }

    record ConversationOwner(String userId, String tenantId) {

        public boolean is(String userId, String tenantId) {
            return this.userId.equals(userId) && this.tenantId.equals(tenantId);
        }
    }
}
