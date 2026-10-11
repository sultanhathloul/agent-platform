package sa.ncai.agent.gateway.admin;

import io.swagger.v3.oas.annotations.media.Schema;

/** A matched region of the tested text, by character offsets. */
public record PolicySpan(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) int start,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) int end,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String entity,
        Double score) {}
