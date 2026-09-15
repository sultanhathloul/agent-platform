package sa.ncai.agent.gateway.memory;

import io.swagger.v3.oas.annotations.media.Schema;

/** One of the caller's memory items (README §8), as the agent returns it. */
public record MemoryItem(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String id,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED,
                description = "preference | fact_about_user | project_context | decision") String kind,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String text,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) double confidence,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "date-time") String createdAt,
        @Schema(format = "date-time", description = "Absent while the item has no expiry.") String expiresAt) {}
