package sa.ncai.agent.gateway.settings;

import io.swagger.v3.oas.annotations.media.Schema;
import java.util.List;

/** The versions, newest first. */
public record SettingsVersions(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<SettingsVersionView> versions) {}
