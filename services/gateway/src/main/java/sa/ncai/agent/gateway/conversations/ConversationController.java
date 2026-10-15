package sa.ncai.agent.gateway.conversations;

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
import java.time.Instant;
import java.time.format.DateTimeParseException;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.regex.Pattern;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
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
import sa.ncai.agent.gateway.chat.ConversationRepository.ConversationOwner;
import sa.ncai.agent.gateway.proxy.AgentClient;
import sa.ncai.agent.gateway.proxy.AgentReply;
import sa.ncai.agent.gateway.proxy.AgentUnavailableException;

/**
 * The caller's conversations and a conversation's history (ADR-0192 to ADR-0194). The agent owns
 * the data; the gateway names the caller from the verified token only (ADR-0059) and checks a
 * conversation's owner itself before the agent checks it again. Both reads are audited fail-closed
 * (ADR-0042), with counts and never message text. Read-only: the product deletes nothing (ADR-0166).
 */
@RestController
@Tag(name = "conversations")
public class ConversationController {

    static final String LIST = "conversation.list";
    static final String READ = "conversation.read";
    static final int DEFAULT_LIMIT = 30;
    static final int MAX_LIMIT = 100;

    private static final Pattern UUID_PATTERN =
            Pattern.compile("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$");

    private final AgentClient agent;
    private final AuditTrail audit;
    private final ErrorResponses errors;
    private final ConversationRepository conversations;
    private final ObjectMapper mapper;

    public ConversationController(AgentClient agent, AuditTrail audit, ErrorResponses errors,
            ConversationRepository conversations, ObjectMapper mapper) {
        this.agent = agent;
        this.audit = audit;
        this.errors = errors;
        this.conversations = conversations;
        this.mapper = mapper;
    }

    @GetMapping(path = "/v1/conversations", produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(
            summary = "The caller's conversations, newest first",
            responses = {
                @ApiResponse(responseCode = "200", description = "A page of the caller's conversations"),
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
    public ResponseEntity<ApiEnvelope<ConversationList>> list(
            @Parameter(description = "Page size, 1 to 100; 30 when absent.")
            @RequestParam(name = "limit", required = false) String limit,
            @Parameter(description = "`next_before` of the previous page (ISO-8601 instant).")
            @RequestParam(name = "before", required = false) String before,
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        long started = System.nanoTime();
        GatewayPrincipal caller = GatewayPrincipal.from(jwt);
        Integer size = limit(limit);
        Optional<Instant> cursor;
        try {
            cursor = Optional.ofNullable(before).map(Instant::parse);
        } catch (DateTimeParseException e) {
            cursor = null;
        }
        if (size == null || cursor == null) {
            // Rejected by validation, so not audited (ADR-0013).
            return failed(request, response, ErrorCode.INVALID_REQUEST);
        }
        if (!audit.reachable()) {
            return failed(request, response, ErrorCode.AUDIT_UNAVAILABLE);
        }
        Call call = new Call(request, response, caller, LIST, null, started);
        String uri = "/v1/conversations?limit=" + size + cursor.map(c -> "&before=" + c).orElse("");
        AgentReply reply = call.agent(uri);
        if (reply == null) {
            return null;
        }
        if (reply.status() != 200) {
            return call.refuse(ErrorCode.AGENT_UNAVAILABLE, AuditStatus.ERROR);
        }
        ConversationList list = mapper.readValue(reply.body(), ConversationList.class);
        call.detail.put("conversations", list.conversations().size());
        return call.ok(list);
    }

    @GetMapping(path = "/v1/conversations/{conversationId}/messages", produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(
            summary = "A conversation's messages, oldest first",
            responses = {
                @ApiResponse(responseCode = "200", description = "The conversation's messages"),
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
    public ResponseEntity<ApiEnvelope<ConversationMessages>> messages(
            @PathVariable("conversationId") String conversationId,
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        long started = System.nanoTime();
        GatewayPrincipal caller = GatewayPrincipal.from(jwt);
        if (!UUID_PATTERN.matcher(conversationId).matches()) {
            return failed(request, response, ErrorCode.INVALID_REQUEST);
        }
        String conversation = conversationId.toLowerCase(Locale.ROOT);
        if (!audit.reachable()) {
            return failed(request, response, ErrorCode.AUDIT_UNAVAILABLE);
        }
        Call call = new Call(request, response, caller, READ, conversation, started);
        Optional<ConversationOwner> owner = conversations.ownerOf(conversation);
        if (owner.isPresent() && !owner.get().is(caller.userId(), caller.tenantId())) {
            return call.refuse(ErrorCode.CONVERSATION_FORBIDDEN, AuditStatus.DENIED);
        }
        if (owner.isEmpty()) {
            // Nobody has used this conversation yet: it holds nothing, and reading binds nobody.
            call.detail.put("messages", 0);
            return call.ok(new ConversationMessages(conversation, List.of()));
        }
        AgentReply reply = call.agent("/v1/conversations/" + conversation + "/messages");
        if (reply == null) {
            return null;
        }
        if (reply.status() == 403) {
            return call.refuse(ErrorCode.CONVERSATION_FORBIDDEN, AuditStatus.DENIED);
        }
        if (reply.status() != 200) {
            return call.refuse(ErrorCode.AGENT_UNAVAILABLE, AuditStatus.ERROR);
        }
        ConversationMessages messages = mapper.readValue(reply.body(), ConversationMessages.class);
        call.detail.put("messages", messages.messages().size());
        return call.ok(messages);
    }

    /** The page size, or null when it is not a whole number from 1 to MAX_LIMIT. */
    private static Integer limit(String value) {
        if (value == null) {
            return DEFAULT_LIMIT;
        }
        try {
            int size = Integer.parseInt(value);
            return size >= 1 && size <= MAX_LIMIT ? size : null;
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

    /** One audited request: its refusal, its success, and the agent call between. */
    private final class Call {
        private final HttpServletRequest request;
        private final HttpServletResponse response;
        private final GatewayPrincipal caller;
        private final String action;
        private final String conversationId;
        private final Map<String, Object> detail = new LinkedHashMap<>();
        private final long started;

        Call(HttpServletRequest request, HttpServletResponse response, GatewayPrincipal caller, String action,
                String conversationId, long started) {
            this.request = request;
            this.response = response;
            this.caller = caller;
            this.action = action;
            this.conversationId = conversationId;
            this.started = started;
        }

        private String requestId() {
            return RequestIdFilter.requestId(request);
        }

        private AuditEvent event(AuditStatus status) {
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
                return ConversationController.this.agent.exchange(HttpMethod.GET, uri, null, caller, requestId());
            } catch (AgentUnavailableException e) {
                refuse(ErrorCode.AGENT_UNAVAILABLE, AuditStatus.ERROR);
                return null;
            }
        }
    }
}
