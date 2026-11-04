package sa.ncai.agent.gateway.files;

import com.fasterxml.jackson.databind.ObjectMapper;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.Parameter;
import io.swagger.v3.oas.annotations.media.ArraySchema;
import io.swagger.v3.oas.annotations.media.Content;
import io.swagger.v3.oas.annotations.media.Schema;
import io.swagger.v3.oas.annotations.responses.ApiResponse;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.time.OffsetDateTime;
import java.time.format.DateTimeParseException;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
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
import sa.ncai.agent.gateway.proxy.AgentClient;
import sa.ncai.agent.gateway.proxy.AgentReply;
import sa.ncai.agent.gateway.proxy.AgentUnavailableException;

/**
 * The page «ملفاتي» / "My files" (my-files PR, owner decision 1, ADR-0442): every file the caller uploaded in any of
 * their conversations, knowledge base documents never among them. Checked twice: the agent lists the caller's own
 * files only (X-User-Id and X-Tenant-Id, from the verified token only, ADR-0059), and the gateway then keeps only the
 * files whose conversation its own conversations table says is the caller's, so another user's file never leaves even
 * if the agent erred. Audited fail-closed (ADR-0042) as {@code file.library} with the filters and the count, never the
 * query's text (G8). A file is downloaded and deleted through its conversation's attachment path
 * ({@link sa.ncai.agent.gateway.attachments.AttachmentController}), which checks and audits it again.
 */
@RestController
@Tag(name = "files")
public class FileController {

    static final String LIBRARY = "file.library";
    static final int MAX_QUERY_CHARS = 200;
    static final Set<String> KINDS = Set.of("pdf", "docx", "xlsx", "txt", "md");
    static final Set<String> STATUSES = Set.of("pending", "indexed", "failed");

    private static final Logger log = LoggerFactory.getLogger(FileController.class);
    private static final Pattern UUID_PATTERN =
            Pattern.compile("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$");

    private final AgentClient agent;
    private final AuditTrail audit;
    private final ErrorResponses errors;
    private final ConversationRepository conversations;
    private final ObjectMapper mapper;

    public FileController(AgentClient agent, AuditTrail audit, ErrorResponses errors,
            ConversationRepository conversations, ObjectMapper mapper) {
        this.agent = agent;
        this.audit = audit;
        this.errors = errors;
        this.conversations = conversations;
        this.mapper = mapper;
    }

    @GetMapping(path = "/v1/files", produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(
            summary = "The caller's files: every file they uploaded in any conversation, newest first",
            description = "Searched by name or conversation title with q, filtered by type, status and upload date. "
                    + "Knowledge base documents are never listed. Checked twice: the agent lists the caller's own "
                    + "files only, and the gateway drops any file whose conversation is not the caller's in its "
                    + "conversations table. Audited as file.library with the filters and the count, never the query.",
            responses = {
                @ApiResponse(responseCode = "200", description = "A page of the caller's files"),
                @ApiResponse(responseCode = "400", description = "invalid_request", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "401", description = "unauthorized", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "502", description = "agent_unavailable", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "503", description = "audit_unavailable", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class)))
            })
    public ResponseEntity<ApiEnvelope<FileDtos.FilePage>> library(
            @Parameter(description = "Words of the file's name or its conversation's title; empty lists the newest.")
            @RequestParam(name = "q", required = false) String q,
            @Parameter(description = "Only files of these types (repeatable).",
                    array = @ArraySchema(schema = @Schema(allowableValues = {"pdf", "docx", "xlsx", "txt", "md"})))
            @RequestParam(name = "kind", required = false) List<String> kind,
            @Parameter(schema = @Schema(allowableValues = {"pending", "indexed", "failed"}))
            @RequestParam(name = "status", required = false) String status,
            @Parameter(description = "Uploaded on or after this time (ISO 8601).", schema = @Schema(format = "date-time"))
            @RequestParam(name = "since", required = false) String since,
            @Parameter(description = "Uploaded before this time (ISO 8601).", schema = @Schema(format = "date-time"))
            @RequestParam(name = "until", required = false) String until,
            @Parameter(description = "`next_before` of the previous page.", schema = @Schema(format = "date-time"))
            @RequestParam(name = "before", required = false) String before,
            @Parameter(schema = @Schema(type = "integer", minimum = "1", maximum = "100"))
            @RequestParam(name = "limit", required = false) String limit,
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        long started = System.nanoTime();
        GatewayPrincipal caller = GatewayPrincipal.from(jwt);
        String query = q == null ? "" : q.strip();
        List<String> kinds = kind == null ? List.of() : kind.stream().distinct().toList();
        Integer size = limit == null ? Integer.valueOf(30) : pageSize(limit);
        if (query.length() > MAX_QUERY_CHARS || size == null || kinds.size() > KINDS.size()
                || !KINDS.containsAll(kinds) || (status != null && !STATUSES.contains(status))
                || !instant(since) || !instant(until) || !instant(before)) {
            // Rejected by validation, so not audited (ADR-0013).
            errors.write(request, response, ErrorCode.INVALID_REQUEST);
            return null;
        }
        if (!audit.reachable()) {
            errors.write(request, response, ErrorCode.AUDIT_UNAVAILABLE);
            return null;
        }
        Map<String, Object> detail = new LinkedHashMap<>();
        // Content-free (G8): the query's length and the filters, never its text.
        detail.put("query_chars", query.length());
        if (!kinds.isEmpty()) {
            detail.put("kinds", kinds);
        }
        if (status != null) {
            detail.put("status", status);
        }
        if (since != null || until != null) {
            detail.put("dated", true);
        }
        // A template filled with the values, each encoded whole by the client (never by hand).
        StringBuilder uri = new StringBuilder("/v1/files?limit={limit}");
        Map<String, Object> values = new LinkedHashMap<>();
        values.put("limit", size);
        param(uri, values, "q", query.isEmpty() ? null : query);
        for (int i = 0; i < kinds.size(); i++) {
            param(uri, values, "kind", "kind" + i, kinds.get(i));
        }
        param(uri, values, "status", status);
        param(uri, values, "since", since);
        param(uri, values, "until", until);
        param(uri, values, "before", before);
        String requestId = RequestIdFilter.requestId(request);
        AgentReply reply;
        try {
            reply = agent.exchange(HttpMethod.GET, uri.toString(), values, null, caller, requestId);
        } catch (AgentUnavailableException e) {
            log.warn("{}", e.getMessage());
            return refuse(request, response, caller, detail, started, ErrorCode.AGENT_UNAVAILABLE, AuditStatus.ERROR);
        }
        if (reply.status() != 200) {
            log.warn("agent answered {} to a file library read", reply.status());
            return refuse(request, response, caller, detail, started, ErrorCode.AGENT_UNAVAILABLE, AuditStatus.ERROR);
        }
        FileDtos.FilePage page = mapper.readValue(reply.body(), FileDtos.FilePage.class);
        // The gateway's own check: only files whose conversation the caller owns here.
        List<String> ids = page.files().stream()
                .map(f -> f.conversationId() == null ? "" : f.conversationId().toLowerCase(Locale.ROOT))
                .filter(id -> UUID_PATTERN.matcher(id).matches())
                .distinct()
                .toList();
        Set<String> owned = conversations.ownedBy(ids, caller.userId(), caller.tenantId());
        List<FileDtos.FileItem> kept = page.files().stream()
                .filter(f -> f.conversationId() != null && owned.contains(f.conversationId().toLowerCase(Locale.ROOT)))
                .toList();
        if (kept.size() < page.files().size()) {
            log.warn("the agent listed {} file(s) of conversations the caller does not own here",
                    page.files().size() - kept.size());
            detail.put("dropped", page.files().size() - kept.size());
        }
        detail.put("files", kept.size());
        if (!audit.append(event(requestId, caller, detail, started, AuditStatus.OK))) {
            errors.write(request, response, ErrorCode.AUDIT_UNAVAILABLE);
            return null;
        }
        return ResponseEntity.ok(ApiEnvelope.ok(requestId, new FileDtos.FilePage(kept, page.nextBefore())));
    }

    private ResponseEntity<ApiEnvelope<FileDtos.FilePage>> refuse(HttpServletRequest request,
            HttpServletResponse response, GatewayPrincipal caller, Map<String, Object> detail, long started,
            ErrorCode code, AuditStatus status) throws IOException {
        detail.put("error_code", code.code());
        boolean written = audit.append(event(RequestIdFilter.requestId(request), caller, detail, started, status));
        errors.write(request, response, written ? code : ErrorCode.AUDIT_UNAVAILABLE);
        return null;
    }

    private static AuditEvent event(String requestId, GatewayPrincipal caller, Map<String, Object> detail,
            long started, AuditStatus status) {
        Map<String, Object> recorded = new LinkedHashMap<>(detail);
        recorded.put("latency_ms", (System.nanoTime() - started) / 1_000_000);
        return new AuditEvent(requestId, caller.userId(), caller.tenantId(), LIBRARY, null, status, recorded);
    }

    private static void param(StringBuilder uri, Map<String, Object> values, String name, String value) {
        param(uri, values, name, name, value);
    }

    private static void param(StringBuilder uri, Map<String, Object> values, String name, String variable,
            String value) {
        if (value != null && !value.isEmpty()) {
            uri.append('&').append(name).append("={").append(variable).append('}');
            values.put(variable, value);
        }
    }

    /** Null, or an ISO 8601 date-time with an offset. */
    private static boolean instant(String value) {
        if (value == null) {
            return true;
        }
        if (value.length() > 40) {
            return false;
        }
        try {
            OffsetDateTime.parse(value);
            return true;
        } catch (DateTimeParseException e) {
            return false;
        }
    }

    private static Integer pageSize(String value) {
        if (value.length() > 3) {
            return null;
        }
        try {
            int number = Integer.parseInt(value);
            return number >= 1 && number <= 100 ? number : null;
        } catch (NumberFormatException e) {
            return null;
        }
    }
}
