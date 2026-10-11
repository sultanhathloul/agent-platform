package sa.ncai.agent.gateway.attachments;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import io.swagger.v3.oas.annotations.media.Schema;

/**
 * An attachment as a client sees it (ADR-0138): its status and, when failed, the reason code the
 * web shows in Arabic. The agent's object key never leaves the gateway.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record AttachmentView(
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String id,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String conversationId,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String name,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String mime,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = {"pdf", "docx", "xlsx", "txt", "md"})
        String kind,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED) long size,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = {"pending", "indexed", "failed"})
        String status,
        @Schema(description = "Why ingestion failed (failed only), e.g. scanned_unsupported.") String reason,
        Integer pages,
        Integer chunks,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "date-time") String createdAt,
        @Schema(format = "date-time") String indexedAt,
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "date-time",
                description = "30 days after the conversation's last turn (ADR-0125).") String expiresAt) {}
