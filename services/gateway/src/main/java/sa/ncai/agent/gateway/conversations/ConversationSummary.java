package sa.ncai.agent.gateway.conversations;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import io.swagger.v3.oas.annotations.media.Schema;

/** One conversation in the list: its id, its first question as title, and its latest turn. */
@JsonIgnoreProperties(ignoreUnknown = true)
public record ConversationSummary(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String id,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED,
                description = "The conversation's first question, at most 80 characters.") String title,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "date-time") String lastTurnAt) {}
