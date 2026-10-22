package sa.ncai.agent.gateway.settings;

import io.swagger.v3.oas.annotations.media.Schema;
import java.util.List;

/** What a draft would produce, before it is saved: nothing is written. */
public record SettingsPreview(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) boolean valid,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<SettingsProblem> problems,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<SettingField> fields,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<TimeoutPair> tree,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, description = "The keys whose value in force the draft changes") List<String> changed) {}
