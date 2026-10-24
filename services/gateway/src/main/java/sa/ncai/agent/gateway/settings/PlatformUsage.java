package sa.ncai.agent.gateway.settings;

import io.swagger.v3.oas.annotations.media.Schema;
import java.util.List;

/** What is in use now, from the agent's in-memory counters (ADR-0282). */
public record PlatformUsage(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) TurnsUsage turns,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<ServerUsage> mcp,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) int registrationsInBackground,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, description = "Tasks that ignored their cancellation since the agent started") int tasksAbandoned) {}
