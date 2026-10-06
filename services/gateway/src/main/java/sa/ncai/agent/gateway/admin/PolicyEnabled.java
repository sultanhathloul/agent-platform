package sa.ncai.agent.gateway.admin;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.NotNull;

public record PolicyEnabled(@NotNull @Schema(requiredMode = Schema.RequiredMode.REQUIRED) Boolean enabled) {}
