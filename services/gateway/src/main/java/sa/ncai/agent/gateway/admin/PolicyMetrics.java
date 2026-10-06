package sa.ncai.agent.gateway.admin;

import io.swagger.v3.oas.annotations.media.Schema;
import java.util.List;

/** Matched policy evaluations of real turns per UTC day, policy and action (README §10.4). */
public record PolicyMetrics(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) int days,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<PolicyMatchRow> rows) {}
