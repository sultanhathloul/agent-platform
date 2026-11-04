package sa.ncai.agent.gateway.files;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import io.swagger.v3.oas.annotations.media.Schema;
import java.util.List;

/** The page «ملفاتي»'s bodies (my-files PR, owner decision 1, ADR-0442), as the agent answers them. */
public final class FileDtos {

    private FileDtos() {}

    /** One of the caller's uploaded files, with the conversation it was uploaded in; never its object key. */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record FileItem(
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "uuid") String id,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "uuid") String conversationId,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED,
                    description = "The conversation's first question, as stored; empty when it has none.")
            String conversationTitle,
            @Schema(format = "date-time", description = "When the conversation began.") String conversationCreatedAt,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String name,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String mime,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = {"pdf", "docx", "xlsx", "txt", "md"})
            String kind,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, description = "Bytes.") long size,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = {"pending", "indexed", "failed"})
            String status,
            @Schema(description = "Why ingestion failed (failed only).") String reason,
            @Schema(description = "Pages, when known.") Integer pages,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "date-time", description = "When it was uploaded.")
            String createdAt) {}

    /** A page of the caller's files, newest first. */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record FilePage(
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<FileItem> files,
            @Schema(format = "date-time", description = "`before` of the next page; absent on the last.")
            String nextBefore) {}
}
