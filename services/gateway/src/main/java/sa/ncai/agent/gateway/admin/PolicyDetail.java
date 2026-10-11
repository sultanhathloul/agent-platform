package sa.ncai.agent.gateway.admin;

import io.swagger.v3.oas.annotations.media.Schema;
import java.util.List;

/** The current version and the whole version history, newest first. */
public record PolicyDetail(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) PolicyView current,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<PolicyVersionView> versions) {}
