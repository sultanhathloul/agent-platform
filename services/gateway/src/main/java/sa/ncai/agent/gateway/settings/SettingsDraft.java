package sa.ncai.agent.gateway.settings;

import com.fasterxml.jackson.databind.JsonNode;
import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.NotNull;
import java.util.Map;

/** Every override a new version holds; a key left out is its default. */
public record SettingsDraft(
        @NotNull @Schema(requiredMode = Schema.RequiredMode.REQUIRED) Map<String, JsonNode> overrides,
        @Schema(description = "The version the draft was made from; another save first is 409 settings_conflict")
                Integer expectedVersion) {}
