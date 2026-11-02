package sa.ncai.agent.gateway.reports;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import io.swagger.v3.oas.annotations.media.Schema;
import java.util.List;

/** The report API's bodies (reports PR, I5, I6), as the agent answers them (its ReportView and ReportVersionView). */
public final class ReportDtos {

    private ReportDtos() {}

    /** One version of a report, without its page. */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record ReportVersionInfo(
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, minimum = "1") int version,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String title,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, description = "Bytes of the stored page.") long size,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = {"ar", "en"}) String locale,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "date-time") String createdAt,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, description = "Figures the page shows.") int figures,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED,
                    description = "Of them, those labelled general knowledge: matched to no source (D2, ADR-0388).") int general) {}

    /** A report and its versions, oldest first; none is ever removed (D4). */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record ReportView(
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "uuid") String id,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "uuid") String conversationId,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, description = "The latest version's title.")
            String title,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, minimum = "1") int latestVersion,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "date-time") String createdAt,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "date-time") String updatedAt,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<ReportVersionInfo> versions) {}

    /** One report of the caller's library (report library PR, owner decisions 3 and 4, ADR-0436). */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record LibraryItem(
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "uuid") String id,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "uuid") String conversationId,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, description = "The latest version's title.")
            String title,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, minimum = "1") int latestVersion,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, minimum = "1",
                    description = "The version the search matched best; the latest without a query.") int matchedVersion,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, description = "The spec's subtitle and period.")
            String subject,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, description = "Section titles, in order.")
            List<String> sections,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, description = "The names of the sources it cites.")
            List<String> sources,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED,
                    description = "general, file, or kb:<slug>: the source filter's values.") List<String> sourceKinds,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "date-time") String createdAt,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "date-time") String updatedAt) {}

    /** A page of the caller's library: best match first with a query, newest first without. */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record LibraryPage(
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<LibraryItem> reports,
            @Schema(format = "date-time", description = "`before` of the next page; absent on the last.")
            String nextBefore) {}

    /** A conversation's reports, oldest first. */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record ReportList(@Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<ReportView> reports) {}

    /** A knowledge base's name in both languages. */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record KbName(
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String ar,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String en) {}

    /** A citation of the page's sources section, as the `citation` event carries it. */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record ReportCitation(
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String id,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String title,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String source,
            String url,
            String kb,
            String locator,
            String documentId,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<Integer> pages,
            KbName kbName,
            String publisher,
            @Schema(description = "ISO 8601 date.") String publishedOn) {}

    /** One version for the panel: its metadata and its stored page (the code view), without the kit. */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record ReportVersionView(
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "uuid") String id,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "uuid") String conversationId,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, minimum = "1") int version,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String title,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) long size,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, allowableValues = {"ar", "en"}) String locale,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "date-time") String createdAt,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) int figures,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) int general,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<ReportCitation> citations,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED,
                    description = "The stored page; the kit stands at its <!--report-kit--> marker.") String html) {}

    /**
     * A short-lived link for the panel's sandboxed frame: a frame's navigation carries no bearer token,
     * so the link itself names the caller, the report and the version, signed by the gateway (I4).
     */
    public record ViewLink(
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED,
                    description = "The page, on the gateway's origin; valid until expires_at, for this version only.")
            String url,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "date-time") String expiresAt) {}
}
