package sa.ncai.agent.gateway.settings;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.Min;

/** An earlier version whose overrides come back as a new version. */
public record SettingsRevert(
        @Min(1) @Schema(requiredMode = Schema.RequiredMode.REQUIRED) int version,
        Integer expectedVersion) {}
