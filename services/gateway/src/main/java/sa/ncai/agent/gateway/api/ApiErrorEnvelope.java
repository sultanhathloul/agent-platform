package sa.ncai.agent.gateway.api;

import io.swagger.v3.oas.annotations.media.Schema;

/** The error form of {@link ApiEnvelope}, as a concrete type for error responses and the OpenAPI spec. */
public record ApiErrorEnvelope(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String requestId,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) ApiError error) {}
