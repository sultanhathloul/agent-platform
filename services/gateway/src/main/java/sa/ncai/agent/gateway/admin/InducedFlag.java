package sa.ncai.agent.gateway.admin;

import io.swagger.v3.oas.annotations.media.Schema;

/** A policy whose exception flag a change set or cleared (ADR-0115). */
public record InducedFlag(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String policyRef,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) boolean exception) {}
