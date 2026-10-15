-- Conversation history (ADR-0192): the list of a user's conversations comes from the ownership
-- table. `title` is the conversation's first question as stored (at most 80 characters), set once;
-- an empty title marks a thread read without an exchange. `last_turn_at` is the end of the latest
-- finished turn; a conversation is listed once it has one.
ALTER TABLE agent.conversation_owners ADD COLUMN title text;
ALTER TABLE agent.conversation_owners ADD COLUMN last_turn_at timestamptz;

CREATE INDEX conversation_owners_list_idx
    ON agent.conversation_owners (tenant_id, user_id, last_turn_at DESC)
    WHERE last_turn_at IS NOT NULL;
