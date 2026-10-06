package sa.ncai.agent.gateway.admin;

import io.swagger.v3.oas.annotations.media.Schema;

/** A policy's current version and switch, and who changed it. */
public record PolicyView(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) PolicyDocument policy,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "date-time") String createdAt,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String createdBy,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "date-time") String updatedAt,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String updatedBy) {}
