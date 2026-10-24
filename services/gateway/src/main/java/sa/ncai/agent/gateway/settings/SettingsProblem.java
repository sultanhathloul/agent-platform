package sa.ncai.agent.gateway.settings;

import io.swagger.v3.oas.annotations.media.Schema;
import java.util.Map;

/** Why a draft cannot be saved: a rule it breaks or a range it leaves, in plain words (ADR-0278). */
public record SettingsProblem(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String rule,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String family,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) Map<String, String> params,
        Double innerValue,
        Double outerValue,
        Double minimum,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String messageAr,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String messageEn) {}
