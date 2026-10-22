package sa.ncai.agent.gateway.settings;

import com.fasterxml.jackson.databind.JsonNode;
import io.swagger.v3.oas.annotations.media.Schema;
import java.util.Map;

/** One version of the overrides, with who made it (ADR-0123). */
public record SettingsVersionView(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) long version,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = {"seed", "save", "revert", "restore"}) String change,
        Integer revertedTo,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) Map<String, JsonNode> overrides,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "date-time") String createdAt,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String createdBy,
        String createdByUsername,
        String createdByName) {}
