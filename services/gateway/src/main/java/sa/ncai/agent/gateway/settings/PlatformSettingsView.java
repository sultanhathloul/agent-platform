package sa.ncai.agent.gateway.settings;

import io.swagger.v3.oas.annotations.media.Schema;
import java.util.List;

/** The platform settings page: every editable value, the read-only ones, and the timeout tree. */
public record PlatformSettingsView(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, description = "The stored version in force; 0 for none") long version,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, description = "When the stored version fails the rules against the files: the rules it "
                + "fails, and the files' values are in force") List<String> ignored,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<SettingField> fields,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<ReadOnlySetting> readOnly,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<TimeoutPair> tree) {}
