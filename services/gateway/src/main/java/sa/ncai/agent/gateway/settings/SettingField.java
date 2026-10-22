package sa.ncai.agent.gateway.settings;

import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.databind.JsonNode;
import io.swagger.v3.oas.annotations.media.Schema;

/** One editable platform setting: its default (the files'), the value in force and its range (ADR-0276). */
public record SettingField(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, description = "The setting's id, e.g. turn.per_user_max or mcp.rag.max_concurrency")
                String key,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = {"turns", "models", "uploads", "mcp"}) String group,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = {"s", "count", "per_minute"}) String unit,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) boolean integer,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, description = "null is a value: no limit of its own") boolean nullable,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, description = "The value is a map from tenant id to a count") boolean perTenant,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) double minimum,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) double maximum,
        @JsonProperty("default")
        @Schema(description = "The files' value (the environment's, where it sets one): a number, a map or null")
                JsonNode defaultValue,
        @Schema(description = "The value in force: the override, or the default") JsonNode value,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) boolean overridden,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, description = "The file and key of the default") String source,
        @Schema(description = "An MCP server's id, for its max_concurrency") String server) {}
