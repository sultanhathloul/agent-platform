package sa.ncai.agent.gateway.reports;

import com.fasterxml.jackson.databind.ObjectMapper;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.Parameter;
import io.swagger.v3.oas.annotations.media.Content;
import io.swagger.v3.oas.annotations.media.Schema;
import io.swagger.v3.oas.annotations.responses.ApiResponse;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.regex.Pattern;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;
import sa.ncai.agent.gateway.GatewayProperties;
import sa.ncai.agent.gateway.api.ApiEnvelope;
import sa.ncai.agent.gateway.api.ApiErrorEnvelope;
import sa.ncai.agent.gateway.api.ErrorCode;
import sa.ncai.agent.gateway.api.ErrorResponses;
import sa.ncai.agent.gateway.api.RequestIdFilter;
import sa.ncai.agent.gateway.audit.AuditEvent;
import sa.ncai.agent.gateway.audit.AuditStatus;
import sa.ncai.agent.gateway.audit.AuditTrail;
import sa.ncai.agent.gateway.auth.GatewayPrincipal;
import sa.ncai.agent.gateway.chat.ConversationRepository;
import sa.ncai.agent.gateway.chat.ConversationRepository.ConversationOwner;
import sa.ncai.agent.gateway.proxy.AgentClient;
import sa.ncai.agent.gateway.proxy.AgentReply;
import sa.ncai.agent.gateway.proxy.AgentUnavailableException;

/**
 * A conversation's interactive reports (reports PR, I5, I6). Only the conversation's owner reads a
 * report, checked twice (I5): the agent finds the report by the caller it is named (X-User-Id and
 * X-Tenant-Id, from the verified token only, ADR-0059), and the gateway then checks that the report's
 * conversation is the caller's in its own conversations table. A report that does not exist and another
 * owner's are the same 404 {@code report_not_found}, so an id reveals nothing. Every read is audited
 * fail-closed (ADR-0042) with the report's id and version, never its title or page.
 *
 * <p>The page itself is not served here: the panel's sandboxed frame loads it by a short-lived view link
 * ({@link ViewTickets}, {@link ReportPageController}); the download is one self-contained file, served as
 * an attachment under {@code Content-Security-Policy: sandbox}, so even a browser that opened it from
 * here would run nothing on the gateway's origin.
 */
@RestController
@Tag(name = "reports")
public class ReportController {

    static final String LIST = "report.list";
    static final String READ = "report.read";
    static final String VIEW_LINK = "report.view_link";
    static final String DOWNLOAD = "report.download";
    /** The agent's version bound (its path validation). */
    static final int MAX_VERSION = 100_000;

    private static final Logger log = LoggerFactory.getLogger(ReportController.class);
    private static final Pattern UUID_PATTERN =
            Pattern.compile("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$");

    private final AgentClient agent;
    private final AuditTrail audit;
    private final ErrorResponses errors;
    private final ConversationRepository conversations;
    private final ObjectMapper mapper;
    private final ViewTickets tickets;
    private final String viewBase;

    public ReportController(AgentClient agent, AuditTrail audit, ErrorResponses errors,
            ConversationRepository conversations, ObjectMapper mapper, ViewTickets tickets,
            GatewayProperties properties) {
        this.agent = agent;
        this.audit = audit;
        this.errors = errors;
        this.conversations = conversations;
        this.mapper = mapper;
        this.tickets = tickets;
        this.viewBase = origin(properties.reports().gatewayPublicUrl()) + ReportPageController.PATH_PREFIX;
    }

    /** An origin without its trailing slash. */
    static String origin(String url) {
        return url.endsWith("/") ? url.substring(0, url.length() - 1) : url;
    }

    @GetMapping(path = "/v1/reports/{reportId}", produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(
            summary = "A report and its versions",
            responses = {
                @ApiResponse(responseCode = "200", description = "The report, its versions oldest first"),
                @ApiResponse(responseCode = "400", description = "invalid_request", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "401", description = "unauthorized", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "404", description = "report_not_found: none of the caller's",
                        content = @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "502", description = "agent_unavailable", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "503", description = "audit_unavailable", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class)))
            })
    public ResponseEntity<ApiEnvelope<ReportDtos.ReportView>> report(
            @Parameter(schema = @Schema(format = "uuid")) @PathVariable("reportId") String reportId,
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        Call call = open(request, response, jwt, READ, reportId, null);
        if (call == null) {
            return null;
        }
        ReportDtos.ReportView view = call.owned();
        return view == null ? null : call.ok(view);
    }

    @GetMapping(path = "/v1/conversations/{conversationId}/reports", produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(
            summary = "A conversation's reports, oldest first",
            responses = {
                @ApiResponse(responseCode = "200", description = "The conversation's reports"),
                @ApiResponse(responseCode = "400", description = "invalid_request", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "401", description = "unauthorized", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "403", description = "conversation_forbidden", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "502", description = "agent_unavailable", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "503", description = "audit_unavailable", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class)))
            })
    public ResponseEntity<ApiEnvelope<ReportDtos.ReportList>> list(
            @Parameter(schema = @Schema(format = "uuid")) @PathVariable("conversationId") String conversationId,
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        long started = System.nanoTime();
        GatewayPrincipal caller = GatewayPrincipal.from(jwt);
        if (!UUID_PATTERN.matcher(conversationId).matches()) {
            // Rejected by validation, so not audited (ADR-0013).
            return failed(request, response, ErrorCode.INVALID_REQUEST);
        }
        String conversation = conversationId.toLowerCase(Locale.ROOT);
        if (!audit.reachable()) {
            return failed(request, response, ErrorCode.AUDIT_UNAVAILABLE);
        }
        Call call = new Call(request, response, caller, LIST, started);
        call.conversationId = conversation;
        // As the conversation's messages (ADR-0194): another owner's is refused before the agent is
        // called, and one nobody has used holds nothing.
        Optional<ConversationOwner> owner = conversations.ownerOf(conversation);
        if (owner.isPresent() && !owner.get().is(caller.userId(), caller.tenantId())) {
            return call.refuse(ErrorCode.CONVERSATION_FORBIDDEN, AuditStatus.DENIED);
        }
        if (owner.isEmpty()) {
            call.detail.put("reports", 0);
            return call.ok(new ReportDtos.ReportList(List.of()));
        }
        AgentReply reply = call.agent("/v1/conversations/" + conversation + "/reports");
        if (reply == null) {
            return null;
        }
        if (reply.status() != 200) {
            return call.refuse(ErrorCode.AGENT_UNAVAILABLE, AuditStatus.ERROR);
        }
        ReportDtos.ReportList list = mapper.readValue(reply.body(), ReportDtos.ReportList.class);
        // The agent lists the caller's only; one of a conversation the caller does not own is dropped.
        List<ReportDtos.ReportView> owned =
                list.reports().stream().filter(r -> conversation.equals(r.conversationId())).toList();
        call.detail.put("reports", owned.size());
        return call.ok(new ReportDtos.ReportList(owned));
    }

    @GetMapping(path = "/v1/reports/{reportId}/versions/{version}", produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(
            summary = "One version of a report: its metadata, citations and stored page (the panel's code view)",
            responses = {
                @ApiResponse(responseCode = "200", description = "The version"),
                @ApiResponse(responseCode = "400", description = "invalid_request", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "401", description = "unauthorized", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "404", description = "report_not_found: no such version of the caller's",
                        content = @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "502", description = "agent_unavailable", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "503", description = "audit_unavailable or report_unavailable",
                        content = @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                schema = @Schema(implementation = ApiErrorEnvelope.class)))
            })
    public ResponseEntity<ApiEnvelope<ReportDtos.ReportVersionView>> version(
            @Parameter(schema = @Schema(format = "uuid")) @PathVariable("reportId") String reportId,
            @Parameter(description = "The version, from 1.", schema = @Schema(type = "integer", minimum = "1",
                    maximum = "100000"))
            @PathVariable("version") String version,
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        Call call = open(request, response, jwt, READ, reportId, version);
        if (call == null) {
            return null;
        }
        AgentReply reply = call.agent("/v1/reports/" + call.reportId + "/versions/" + call.version);
        if (reply == null || !call.found(reply)) {
            return null;
        }
        ReportDtos.ReportVersionView view = mapper.readValue(reply.body(), ReportDtos.ReportVersionView.class);
        if (!call.owns(view.conversationId())) {
            return call.refuse(ErrorCode.REPORT_NOT_FOUND, AuditStatus.DENIED);
        }
        return call.ok(view);
    }

    @PostMapping(path = "/v1/reports/{reportId}/versions/{version}/view", produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(
            summary = "A short-lived link to the version's page, for the panel's sandboxed frame",
            description = "The link is valid for 60 seconds and for this caller and version only; the page it "
                    + "serves links the platform's report kit and runs in a frame sandboxed to scripts only (I4). "
                    + "Ask again for a new link when the frame is loaded again.",
            responses = {
                @ApiResponse(responseCode = "200", description = "The link and when it expires"),
                @ApiResponse(responseCode = "400", description = "invalid_request", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "401", description = "unauthorized", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "404", description = "report_not_found: no such version of the caller's",
                        content = @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "502", description = "agent_unavailable", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "503", description = "audit_unavailable", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class)))
            })
    public ResponseEntity<ApiEnvelope<ReportDtos.ViewLink>> view(
            @Parameter(schema = @Schema(format = "uuid")) @PathVariable("reportId") String reportId,
            @Parameter(description = "The version, from 1.", schema = @Schema(type = "integer", minimum = "1",
                    maximum = "100000"))
            @PathVariable("version") String version,
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        Call call = open(request, response, jwt, VIEW_LINK, reportId, version);
        if (call == null) {
            return null;
        }
        ReportDtos.ReportView report = call.owned();
        if (report == null) {
            return null;
        }
        if (call.version > report.latestVersion()) {
            return call.refuse(ErrorCode.REPORT_NOT_FOUND, AuditStatus.DENIED);
        }
        ViewTickets.Issued issued = tickets.issue(call.caller, call.reportId, call.version);
        call.detail.put("expires_at", issued.expiresAt().toString());
        return call.ok(new ReportDtos.ViewLink(viewBase + issued.ticket(), issued.expiresAt().toString()));
    }

    @GetMapping(path = "/v1/reports/{reportId}/versions/{version}/download")
    @Operation(
            summary = "The version as one self-contained HTML file that opens offline",
            description = "The kit's stylesheet, scripts and fonts are inlined; the file fetches nothing. Served "
                    + "as an attachment under `Content-Security-Policy: sandbox`. Errors are JSON envelopes.",
            responses = {
                @ApiResponse(responseCode = "200", description = "The file", content = @Content(
                        mediaType = MediaType.TEXT_HTML_VALUE, schema = @Schema(type = "string"))),
                @ApiResponse(responseCode = "400", description = "invalid_request", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "401", description = "unauthorized", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "404", description = "report_not_found: no such version of the caller's",
                        content = @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "502", description = "agent_unavailable", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "503", description = "audit_unavailable or report_unavailable",
                        content = @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                schema = @Schema(implementation = ApiErrorEnvelope.class)))
            })
    public ResponseEntity<byte[]> download(
            @Parameter(schema = @Schema(format = "uuid")) @PathVariable("reportId") String reportId,
            @Parameter(description = "The version, from 1.", schema = @Schema(type = "integer", minimum = "1",
                    maximum = "100000"))
            @PathVariable("version") String version,
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        Call call = open(request, response, jwt, DOWNLOAD, reportId, version);
        if (call == null) {
            return null;
        }
        if (call.owned() == null) {
            return null;
        }
        AgentReply reply = call.agent(
                "/v1/reports/" + call.reportId + "/versions/" + call.version + "/page?form=download");
        if (reply == null || !call.found(reply)) {
            return null;
        }
        call.detail.put("bytes", reply.body().length);
        if (!audit.append(call.event(AuditStatus.OK))) {
            return failed(request, response, ErrorCode.AUDIT_UNAVAILABLE);
        }
        String file = "report-" + call.reportId.substring(0, 8) + "-v" + call.version + ".html";
        return ResponseEntity.ok()
                .header(HttpHeaders.CONTENT_TYPE, "text/html; charset=utf-8")
                .header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename=\"" + file + "\"")
                .header("X-Content-Type-Options", "nosniff")
                .header("Content-Security-Policy", "sandbox")
                .header(HttpHeaders.CACHE_CONTROL, "no-store")
                .body(reply.body());
    }

    /**
     * Validates the path and checks the audit log, writing the refusal when either fails (then null): a
     * malformed id or version is rejected by validation, so not audited (ADR-0013).
     */
    private Call open(HttpServletRequest request, HttpServletResponse response, Jwt jwt, String action,
            String reportId, String version) throws IOException {
        long started = System.nanoTime();
        GatewayPrincipal caller = GatewayPrincipal.from(jwt);
        Integer number = version == null ? Integer.valueOf(0) : version(version);
        if (!UUID_PATTERN.matcher(reportId).matches() || number == null) {
            failed(request, response, ErrorCode.INVALID_REQUEST);
            return null;
        }
        if (!audit.reachable()) {
            failed(request, response, ErrorCode.AUDIT_UNAVAILABLE);
            return null;
        }
        Call call = new Call(request, response, caller, action, started);
        call.reportId = reportId.toLowerCase(Locale.ROOT);
        call.version = number;
        call.detail.put("report_id", call.reportId);
        if (version != null) {
            call.detail.put("version", number);
        }
        return call;
    }

    /** A version number from 1 to MAX_VERSION, or null. */
    private static Integer version(String value) {
        if (value.length() > 6) {
            return null;
        }
        try {
            int number = Integer.parseInt(value);
            return number >= 1 && number <= MAX_VERSION ? number : null;
        } catch (NumberFormatException e) {
            return null;
        }
    }

    /** Writes the error envelope; the response is committed, so nothing is returned to MVC. */
    private <T> ResponseEntity<T> failed(HttpServletRequest request, HttpServletResponse response, ErrorCode code)
            throws IOException {
        errors.write(request, response, code);
        return null;
    }

    /** One audited request: its refusal, its success, and the agent calls between. */
    private final class Call {
        private final HttpServletRequest request;
        private final HttpServletResponse response;
        private final GatewayPrincipal caller;
        private final String action;
        private final Map<String, Object> detail = new LinkedHashMap<>();
        private final long started;
        private String reportId;
        private int version;
        private String conversationId;

        Call(HttpServletRequest request, HttpServletResponse response, GatewayPrincipal caller, String action,
                long started) {
            this.request = request;
            this.response = response;
            this.caller = caller;
            this.action = action;
            this.started = started;
        }

        private String requestId() {
            return RequestIdFilter.requestId(request);
        }

        AuditEvent event(AuditStatus status) {
            Map<String, Object> recorded = new LinkedHashMap<>(detail);
            recorded.put("latency_ms", (System.nanoTime() - started) / 1_000_000);
            return new AuditEvent(requestId(), caller.userId(), caller.tenantId(), action, conversationId, status,
                    recorded);
        }

        /** Audits the refusal, then answers with {@code code}, or with audit_unavailable when the record failed. */
        <T> ResponseEntity<T> refuse(ErrorCode code, AuditStatus status) throws IOException {
            detail.put("error_code", code.code());
            return failed(request, response, audit.append(event(status)) ? code : ErrorCode.AUDIT_UNAVAILABLE);
        }

        <T> ResponseEntity<ApiEnvelope<T>> ok(T body) throws IOException {
            if (!audit.append(event(AuditStatus.OK))) {
                return failed(request, response, ErrorCode.AUDIT_UNAVAILABLE);
            }
            return ResponseEntity.ok(ApiEnvelope.ok(requestId(), body));
        }

        /** The agent's reply, or null when it was unreachable and the refusal is written. */
        AgentReply agent(String uri) throws IOException {
            try {
                return ReportController.this.agent.exchange(HttpMethod.GET, uri, null, caller, requestId());
            } catch (AgentUnavailableException e) {
                log.warn("{}", e.getMessage());
                refuse(ErrorCode.AGENT_UNAVAILABLE, AuditStatus.ERROR);
                return null;
            }
        }

        /** True for the agent's 200; otherwise its refusal is mapped and written. */
        boolean found(AgentReply reply) throws IOException {
            if (reply.status() == 200) {
                return true;
            }
            if (reply.status() == 404) {
                refuse(ErrorCode.REPORT_NOT_FOUND, AuditStatus.DENIED);
            } else if (reply.status() == 503) {
                refuse(ErrorCode.REPORT_UNAVAILABLE, AuditStatus.ERROR);
            } else {
                log.warn("agent answered {} to a report read", reply.status());
                refuse(ErrorCode.AGENT_UNAVAILABLE, AuditStatus.ERROR);
            }
            return false;
        }

        /**
         * The gateway's own check (I5): the report's conversation is the caller's in the gateway's
         * conversations table. A conversation the table does not know is nobody's here.
         */
        boolean owns(String conversation) {
            String id = conversation == null ? "" : conversation.toLowerCase(Locale.ROOT);
            conversationId = id.isEmpty() ? null : id;
            Optional<ConversationOwner> owner = id.isEmpty() ? Optional.empty() : conversations.ownerOf(id);
            boolean owned = owner.isPresent() && owner.get().is(caller.userId(), caller.tenantId());
            if (!owned) {
                log.warn("the agent answered report {} for a conversation the caller does not own here", reportId);
            }
            return owned;
        }

        /** The report as the agent found it for the caller and the gateway's check passed, or null (written). */
        ReportDtos.ReportView owned() throws IOException {
            AgentReply reply = agent("/v1/reports/" + reportId);
            if (reply == null || !found(reply)) {
                return null;
            }
            ReportDtos.ReportView report = mapper.readValue(reply.body(), ReportDtos.ReportView.class);
            if (!owns(report.conversationId())) {
                refuse(ErrorCode.REPORT_NOT_FOUND, AuditStatus.DENIED);
                return null;
            }
            return report;
        }
    }
}
