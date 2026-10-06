package sa.ncai.agent.gateway.admin;

import io.swagger.v3.oas.annotations.media.Schema;
import java.util.List;

/** What a draft would override if saved now, and whether that makes it an exception (ADR-0105). */
public record PolicyOverrides(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<String> overrides,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) boolean exception) {}
