package sa.ncai.agent.gateway.admin;

import io.swagger.v3.oas.annotations.media.Schema;
import java.util.List;

/** Saved anyway, but worth knowing (ADR-0115). */
public record PolicyWarning(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = {"whole_text_cannot_lift_spans"},
                description = "whole_text_cannot_lift_spans: this exception cannot lift span-based redaction")
                String code,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED,
                description = "id@version of the span-based policies it cannot lift") List<String> policies) {}
