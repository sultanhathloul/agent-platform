package sa.ncai.agent.gateway.admin;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

/**
 * One policy, stored ({@code policy_id}: its current version, on or off) or a draft
 * ({@code policy}), against sample text, on the agent's runtime engine path. Nothing is recorded,
 * and the text never enters the audit log.
 */
public record PolicyTestRequest(
        String policyId,
        PolicyDocument policy,
        @NotBlank @Size(max = 20000) @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String text,
        @Schema(description = "Default: the policy's first checkpoint.") String checkpoint,
        @Schema(description = "DATA only: answer | memory_candidate | summary.") String target,
        @Valid @Schema(description = "Whom to evaluate for; default: the admin, with no policy roles.")
                SimulatedPrincipal principal) {}
