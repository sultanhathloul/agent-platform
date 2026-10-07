package sa.ncai.agent.gateway.admin;

import io.swagger.v3.oas.annotations.media.Schema;
import java.util.List;

/** One change of a policy, written by the agent in the change's own transaction (ADR-0108). */
public record PolicyChange(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "date-time") String changedAt,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, description = "id@version the change left current")
                String policyRef,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED,
                allowableValues = {"create", "version", "enable", "disable", "import", "seed"}) String change,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String actor,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) boolean exception,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<String> overrides) {}
