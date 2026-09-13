package sa.ncai.agent.gateway.auth;

import com.fasterxml.jackson.annotation.JsonProperty;
import io.swagger.v3.oas.annotations.media.Schema;
import java.util.List;

/** The principal as the gateway derives it from the token. */
public record MeResponse(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String userId,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String tenantId,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<String> permissions,
        String displayName,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) @JsonProperty("is_admin") boolean admin) {

    static MeResponse of(GatewayPrincipal principal) {
        return new MeResponse(
                principal.userId(), principal.tenantId(), principal.permissions(),
                principal.displayName(), principal.admin());
    }
}
