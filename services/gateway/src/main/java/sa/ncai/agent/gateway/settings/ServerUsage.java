package sa.ncai.agent.gateway.settings;

import io.swagger.v3.oas.annotations.media.Schema;

/** An MCP server's slots in use and its breaker, now. */
public record ServerUsage(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String id,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) int maxConcurrency,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) int fileMaxConcurrency,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) int inUse,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) int backgroundMax,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) int backgroundInUse,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = {"closed", "open", "half_open"}) String breaker,
        @Schema(description = "Seconds since the breaker opened") Double openForS) {}
