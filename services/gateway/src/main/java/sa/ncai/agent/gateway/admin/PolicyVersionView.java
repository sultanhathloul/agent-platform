package sa.ncai.agent.gateway.admin;

import io.swagger.v3.oas.annotations.media.Schema;
import java.util.List;

/** One immutable version of a policy (I2). */
public record PolicyVersionView(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) PolicyDocument policy,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "date-time") String createdAt,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String createdBy,
        @Schema(description = "The creator's username at the time (ADR-0123); absent on older rows")
                String createdByUsername,
        @Schema(description = "The creator's display name at the time (ADR-0123); absent on older rows")
                String createdByName,
        @Schema(description = "What this version overrode when it was saved") List<String> overrides,
        @Schema(description = "Whether it was an exception when it was saved") Boolean exception) {}
