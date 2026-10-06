package sa.ncai.agent.gateway.admin;

import io.swagger.v3.oas.annotations.media.Schema;

/** One immutable version of a policy (I2). */
public record PolicyVersionView(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) PolicyDocument policy,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "date-time") String createdAt,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String createdBy) {}
