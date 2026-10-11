package sa.ncai.agent.gateway.admin;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

/** Policies as one YAML document: {@code policies: [...]}, versions not included. */
public record PolicyYaml(@NotBlank @Size(max = 500000) @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String yaml) {}
