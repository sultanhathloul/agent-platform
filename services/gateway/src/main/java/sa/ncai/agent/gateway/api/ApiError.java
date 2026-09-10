package sa.ncai.agent.gateway.api;

import io.swagger.v3.oas.annotations.media.Schema;

/** Error body; the UI renders its own i18n string by {@code code} and falls back to these messages. */
public record ApiError(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String code,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String messageAr,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String messageEn) {}
