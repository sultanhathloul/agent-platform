package sa.ncai.agent.gateway.admin;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.Size;
import java.util.List;

/** Whom the test panel evaluates a policy for: a tenant and policy roles (README §10.4). */
public record SimulatedPrincipal(
        @Size(min = 1, max = 128) @Schema(description = "Default: default") String tenantId,
        @Size(max = 32) @Schema(description = "Full names, policy:{name}") List<String> policyRoles) {}
