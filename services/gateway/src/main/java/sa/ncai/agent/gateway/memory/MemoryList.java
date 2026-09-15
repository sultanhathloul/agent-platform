package sa.ncai.agent.gateway.memory;

import io.swagger.v3.oas.annotations.media.Schema;
import java.util.List;

/** The caller's memory switch and memory items, newest first. */
public record MemoryList(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED,
                description = "Whether memory is written and retrieved for the caller.") boolean enabled,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<MemoryItem> items) {}
