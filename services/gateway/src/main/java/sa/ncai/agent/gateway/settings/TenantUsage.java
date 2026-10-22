package sa.ncai.agent.gateway.settings;

import io.swagger.v3.oas.annotations.media.Schema;

/** A tenant's turns in flight against its limit; null: the platform's capacity. */
public record TenantUsage(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String tenantId,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) int inFlight,
        Integer limit) {}
