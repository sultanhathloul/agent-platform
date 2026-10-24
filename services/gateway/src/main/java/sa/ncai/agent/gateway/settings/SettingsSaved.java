package sa.ncai.agent.gateway.settings;

import io.swagger.v3.oas.annotations.media.Schema;
import java.util.List;

/** A saved version: what it changed, and the settings now in force. */
public record SettingsSaved(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) long version,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = {"save", "revert", "restore"}) String change,
        Integer revertedTo,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<SettingChange> changes,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) PlatformSettingsView settings) {}
