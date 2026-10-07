package sa.ncai.agent.gateway.admin;

import io.swagger.v3.oas.annotations.media.Schema;
import java.util.List;
import java.util.Map;

/** The CheckResult of the test panel (README §10.4) and what the engine decided. */
public record PolicyTestResult(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String policyRef,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String checkpoint,
        String target,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String action,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) boolean matched,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) double score,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED,
                description = "The checker's explanation in English, as traces record it.") String explanation,
        @Schema(description = "The explanation as a key under the web's policies.explanation.* (rule 7).")
                String explanationKey,
        @Schema(description = "Arguments of explanation_key; `items` lists names separated by newlines.")
                Map<String, String> explanationArgs,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<PolicySpan> spans,
        @Schema(description = "The text after redaction, for text checkpoints.") String outputText,
        String messageAr,
        String messageEn,
        @Schema(description = "The checker's error, when it failed.") String error) {}
