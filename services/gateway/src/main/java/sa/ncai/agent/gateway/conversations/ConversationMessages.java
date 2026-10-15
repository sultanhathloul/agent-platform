package sa.ncai.agent.gateway.conversations;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import io.swagger.v3.oas.annotations.media.Schema;
import java.util.List;

/** A conversation's messages, oldest first, as the model may see them again (ADR-0193). */
@JsonIgnoreProperties(ignoreUnknown = true)
public record ConversationMessages(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String conversationId,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<HistoryMessage> messages) {

    /** One message: the post-guard text, a question's files and a reply's citations when recorded. */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record HistoryMessage(
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = {"user", "assistant"})
            String role,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String text,
            String turnId,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<HistoryAttachment> attachments,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<HistoryCitation> citations) {}

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record HistoryAttachment(
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String id,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String name) {}

    /** A citation as the `citation` event carried it. */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record HistoryCitation(
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String id,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String title,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String source,
            String url,
            String kb,
            String locator,
            String documentId,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<Integer> pages) {}
}
