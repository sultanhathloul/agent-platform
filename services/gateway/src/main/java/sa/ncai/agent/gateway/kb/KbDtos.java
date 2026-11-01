package sa.ncai.agent.gateway.kb;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import java.util.List;

/** Knowledge bases as the admin API and the composer see them (phase 08, ADR-0333 to ADR-0339). */
public final class KbDtos {

    private KbDtos() {}

    /** The slug a KB is known by; its realm role is {@code kb:<slug>:read}. */
    public static final String SLUG = "^[a-z][a-z0-9_-]{0,62}$";

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Label(
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) @NotNull @Size(min = 1, max = 120) String ar,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) @NotNull @Size(min = 1, max = 120) String en) {}

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Description(@Size(max = 500) String ar, @Size(max = 500) String en) {
        public Description {
            ar = ar == null ? "" : ar;
            en = en == null ? "" : en;
        }
    }

    /** An administrator creates a knowledge base (I1). */
    public record KbCreate(
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, pattern = SLUG,
                    description = "Lower-case letters, digits, - and _; its realm role is kb:<slug>:read")
            @NotNull @Pattern(regexp = SLUG) String slug,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) @NotNull @Valid Label name,
            @Valid Description description,
            @Pattern(regexp = "^[a-z][a-z0-9-]{0,39}$") String icon) {}

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record KbView(
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String slug,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) Label name,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) Description description,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String icon,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, description = "kb:<slug>:read") String role,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) int documents,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) int indexed,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "date-time") String createdAt,
            String createdByUsername,
            String createdByName) {}

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record KbList(@Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<KbView> kbs) {}

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record KbDeleted(
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String slug,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, description = "Kept (I1)") String role,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED,
                    description = "False while a backend owes the purge, or keeps its copy (an MCP server)")
            boolean indexPurged) {}

    /** D3: a KB document's source. */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record SourceMetadata(
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) @NotNull @Size(min = 1, max = 300) String title,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) @NotNull @Size(min = 1, max = 200) String publisher,
            @Size(max = 2000) @Pattern(regexp = "^https?://\\S+$") String sourceUrl,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "date") @NotNull
            @Pattern(regexp = "^\\d{4}-\\d{2}-\\d{2}$") String publishedOn,
            @Schema(description = "An earlier version of the same source in this KB")
            @Pattern(regexp = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$") String supersedes) {}

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Warning(
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = {"pages_without_text"}) String code,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, minimum = "1") int count) {}

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record KbDocumentView(
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String id,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String kb,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String name,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = {"pdf", "docx", "xlsx", "txt", "md"})
            String kind,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) long size,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = {"pending", "indexed", "failed"})
            String status,
            String reason,
            Integer pages,
            Integer chunks,
            List<Warning> warnings,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) SourceMetadata metadata,
            @Schema(description = "The document of this KB that supersedes it") String supersededBy,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "date-time") String createdAt,
            @Schema(format = "date-time") String indexedAt,
            String uploadedByUsername,
            String uploadedByName,
            @Schema(description = "The file's SHA-256, computed by the gateway as it stored it (ADR-0392)") String sha256,
            @Schema(description = "Uploaded without its source metadata; cleared when an administrator corrects it")
            boolean metadataMissing,
            @Schema(description = "Indexed by an older version of its kind's extractor; the seed re-indexes it (ADR-0410)")
            boolean reindexDue,
            @Schema(description = "A re-index beside the indexed chunks (ADR-0424): reindexing while it runs, "
                    + "reindex_failed when it could not finish (the document stays indexed on its old chunks)",
                    allowableValues = {"reindexing", "reindex_failed"})
            String reindexStatus,
            @Schema(description = "Why the re-index failed (reindex_failed only)") String reindexReason) {
        public KbDocumentView {
            warnings = warnings == null ? List.of() : List.copyOf(warnings);
        }
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record KbDocumentList(@Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<KbDocumentView> documents) {}

    /** Body of the agent's KB document registration (I2). */
    record Registration(String name, String mime, long size, String objectKey, SourceMetadata metadata, String sha256,
            boolean metadataMissing) {}

    // ------------------------------------------------------------------ the composer (I3)

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Text(
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String ar,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String en) {}

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record CapabilityUi(
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = {"toggle", "auto", "button"})
            String mode,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) int order) {}

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record CapabilityView(
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String slug,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) Text name,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) Text description,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String icon,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) CapabilityUi ui) {}

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record KbToggle(
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String slug,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) Text name,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) Text description,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String icon) {}

    /** What the composer renders (README §13, I3): the caller's capabilities and readable KBs. */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record CapabilityList(
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<CapabilityView> capabilities,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, description = "Only the KBs the caller may read")
            List<KbToggle> knowledgeBases) {}
}
