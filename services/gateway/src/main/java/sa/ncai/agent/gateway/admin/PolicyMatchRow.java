package sa.ncai.agent.gateway.admin;

import io.swagger.v3.oas.annotations.media.Schema;

public record PolicyMatchRow(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "date") String day,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String policyId,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String action,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) long matches) {}
