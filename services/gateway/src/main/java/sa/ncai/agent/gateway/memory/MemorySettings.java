package sa.ncai.agent.gateway.memory;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.NotNull;

/** The caller's memory switch: off, nothing is written or retrieved; saved items stay until deleted. */
public record MemorySettings(@NotNull @Schema(requiredMode = Schema.RequiredMode.REQUIRED) Boolean enabled) {}
