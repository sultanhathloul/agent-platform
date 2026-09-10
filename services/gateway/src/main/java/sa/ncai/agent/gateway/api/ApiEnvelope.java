package sa.ncai.agent.gateway.api;

import io.swagger.v3.oas.annotations.media.Schema;

/** Response envelope (README §6.3): exactly one of {@code data} or {@code error} is set. */
public record ApiEnvelope<T>(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String requestId,
        T data,
        ApiError error) {

    public static <T> ApiEnvelope<T> ok(String requestId, T data) {
        return new ApiEnvelope<>(requestId, data, null);
    }
}
