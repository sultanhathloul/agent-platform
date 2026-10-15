package sa.ncai.agent.gateway.conversations;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import io.swagger.v3.oas.annotations.media.Schema;
import java.util.List;

/** The caller's conversations with a finished turn, newest first (ADR-0192). */
@JsonIgnoreProperties(ignoreUnknown = true)
public record ConversationList(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<ConversationSummary> conversations,
        @Schema(format = "date-time", description = "Pass as `before` for the next page; absent on the last page.")
        String nextBefore) {}
