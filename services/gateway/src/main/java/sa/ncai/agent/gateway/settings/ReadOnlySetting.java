package sa.ncai.agent.gateway.settings;

import com.fasterxml.jackson.databind.JsonNode;
import io.swagger.v3.oas.annotations.media.Schema;

/** A value only a redeploy changes, shown with where it is set (item 2). */
public record ReadOnlySetting(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String key,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) JsonNode value,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = {"s", "count", "MiB", "cpu_s"}) String unit,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, description = "Where it is set; changing it needs a redeploy") String source) {}
