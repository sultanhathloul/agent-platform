package sa.ncai.agent.gateway.admin;

import io.swagger.v3.oas.annotations.media.Schema;

import java.util.List;

/**
 * A policy's current version and switch, who changed it, and what it overrides among the current
 * versions of its group, recomputed for the whole group on every change (ADR-0108).
 */
public record PolicyView(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) PolicyDocument policy,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "date-time") String createdAt,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String createdBy,
        @Schema(description = "The creator's username at the time (ADR-0123); absent on older rows")
                String createdByUsername,
        @Schema(description = "The creator's display name at the time (ADR-0123); absent on older rows")
                String createdByName,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "date-time") String updatedAt,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String updatedBy,
        @Schema(description = "id@version of the wider policies of its group it overrides now")
                List<String> overrides,
        @Schema(description = "It overrides a stricter policy of its group (ADR-0105, ADR-0108)") Boolean exception,
        @Schema(description = "On a save response only (ADR-0115)") List<PolicyWarning> warnings) {}
