package sa.ncai.agent.gateway.admin;

import io.swagger.v3.oas.annotations.media.Schema;
import java.util.List;

/** The CheckResult of the test panel (README §10.4) and what the engine decided. */
public record PolicyTestResult(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String policyRef,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String checkpoint,
        String target,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String action,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) boolean matched,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) double score,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String explanation,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<PolicySpan> spans,
        @Schema(description = "The text after redaction, for text checkpoints.") String outputText,
        String messageAr,
        String messageEn,
        @Schema(description = "The checker's error, when it failed.") String error) {}
