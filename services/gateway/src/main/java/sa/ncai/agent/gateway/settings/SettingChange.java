package sa.ncai.agent.gateway.settings;

import com.fasterxml.jackson.databind.JsonNode;
import io.swagger.v3.oas.annotations.media.Schema;

/** A value in force a save changed. */
public record SettingChange(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String key,
        JsonNode before,
        JsonNode after) {}
