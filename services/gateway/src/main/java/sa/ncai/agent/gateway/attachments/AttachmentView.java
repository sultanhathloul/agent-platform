package sa.ncai.agent.gateway.attachments;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import io.swagger.v3.oas.annotations.media.Schema;
import java.time.OffsetDateTime;

/**
 * An attachment as a client sees it (ADR-0138): its status and, when failed, the reason code the
 * web shows in Arabic. The agent's object key never leaves the gateway.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record AttachmentView(
        String id,
        String conversationId,
        String name,
        String mime,
        String kind,
        long size,
        @Schema(allowableValues = {"pending", "indexed", "failed"}) String status,
        @Schema(description = "Why ingestion failed (failed only), e.g. scanned_unsupported.") String reason,
        Integer pages,
        Integer chunks,
        OffsetDateTime createdAt,
        OffsetDateTime indexedAt,
        @Schema(description = "30 days after the conversation's last turn (ADR-0125).") OffsetDateTime expiresAt) {}
