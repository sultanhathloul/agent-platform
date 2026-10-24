package sa.ncai.agent.gateway.settings;

import io.swagger.v3.oas.annotations.media.Schema;
import java.util.List;

/** Turns in flight now: counts only, never who. */
public record TurnsUsage(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) int inFlight,
        Integer capacity,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) int perUserMax,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, description = "The busiest user's turns in flight: a count, never who") int topUserInFlight,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<TenantUsage> tenants) {}
