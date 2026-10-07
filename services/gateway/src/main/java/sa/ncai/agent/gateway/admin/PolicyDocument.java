package sa.ncai.agent.gateway.admin;

import com.fasterxml.jackson.databind.JsonNode;
import io.swagger.v3.oas.annotations.media.Schema;
import java.util.List;

/**
 * A guard policy (README §10.1) as the admin API reads and writes it. The agent owns and
 * validates it (I1); on create and on a new version {@code version} is ignored (I2).
 */
public record PolicyDocument(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, pattern = "^[a-z0-9][a-z0-9-]{1,63}$") String id,
        @Schema(description = "Assigned by the agent; ignored on create and on a new version.") Integer version,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String name,
        String description,
        Boolean enabled,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED,
                description = "global | tenant:<id> | role:<name> | kb:<slug> | tool:<name>; role:<name> applies to "
                        + "holders of the realm role policy:<name> (ADR-0107); only global contains another scope "
                        + "(ADR-0108)")
                String scope,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED,
                description = "INPUT, TOOL_LIST, PRE_TOOL, POST_TOOL, OUTPUT, DATA, MODEL_TIER") List<String> checkpoints,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED,
                description = "regex | presidio | keyword_list | llm_judge | tier_ceiling") String checker,
        @Schema(description = "The checker's settings; DATA policies may name `targets`.") JsonNode config,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED,
                description = "allow | block | redact | rewrite | warn | require_approval | log_only") String action,
        @Schema(description = "low | medium | high | critical") String severity,
        String messageAr,
        String messageEn,
        @Schema(description = "open | closed; default by checker (D8)") String onError,
        @Schema(description = "Policies of one group override each other narrow over wide; default: the id.")
                String group) {}
