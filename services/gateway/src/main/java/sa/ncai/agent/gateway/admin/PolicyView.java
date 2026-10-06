package sa.ncai.agent.gateway.admin;

import io.swagger.v3.oas.annotations.media.Schema;

import java.util.List;

/**
 * A policy's current version and switch, who changed it, and what the version overrides as
 * computed when it was saved (ADR-0105).
 */
public record PolicyView(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) PolicyDocument policy,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "date-time") String createdAt,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String createdBy,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "date-time") String updatedAt,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String updatedBy,
        @Schema(description = "id@version of the wider policies of its group this version overrides")
                List<String> overrides,
        @Schema(description = "It overrides a stricter policy of its group (ADR-0105)") Boolean exception) {}
