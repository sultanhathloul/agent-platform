package sa.ncai.agent.gateway.settings;

import io.swagger.v3.oas.annotations.media.Schema;
import java.util.Map;

/** One nested pair of the timeout budget's rules, on the values in force or a draft's (ADR-0278). */
public record TimeoutPair(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String rule,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String family,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, description = "The ids the rule names: capability, tool, server") Map<String, String> params,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String inner,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) double innerValue,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String outer,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) double outerValue,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, description = "inner < outer when true, inner ≤ outer when false") boolean strict,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) boolean holds) {}
