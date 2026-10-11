package sa.ncai.agent.gateway.api;

import io.swagger.v3.oas.annotations.media.Schema;
import java.util.List;

/**
 * Error body; the UI renders its own i18n string by {@code code} and falls back to these messages.
 * {@code details} says what is wrong when there is more to say, e.g. a policy's validation errors.
 */
public record ApiError(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String code,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String messageAr,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String messageEn,
        List<String> details) {

    public ApiError(String code, String messageAr, String messageEn) {
        this(code, messageAr, messageEn, null);
    }

    public ApiError withDetails(List<String> details) {
        return new ApiError(code, messageAr, messageEn, details == null || details.isEmpty() ? null : details);
    }
}
