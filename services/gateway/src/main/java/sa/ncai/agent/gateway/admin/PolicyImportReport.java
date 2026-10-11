package sa.ncai.agent.gateway.admin;

import io.swagger.v3.oas.annotations.media.Schema;
import java.util.List;

/** What an import did, by policy id. */
public record PolicyImportReport(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<String> created,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<String> versioned,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<String> switched,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<String> unchanged) {}
