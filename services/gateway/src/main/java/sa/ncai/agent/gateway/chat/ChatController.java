package sa.ncai.agent.gateway.chat;

import com.fasterxml.jackson.databind.JsonNode;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.Parameter;
import io.swagger.v3.oas.annotations.media.Content;
import io.swagger.v3.oas.annotations.media.Schema;
import io.swagger.v3.oas.annotations.responses.ApiResponse;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import jakarta.validation.Valid;
import java.io.IOException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;
import sa.ncai.agent.gateway.api.ApiErrorEnvelope;
import sa.ncai.agent.gateway.api.ErrorCode;
import sa.ncai.agent.gateway.api.ErrorResponses;
import sa.ncai.agent.gateway.api.RequestIdFilter;
import sa.ncai.agent.gateway.audit.AuditEvent;
import sa.ncai.agent.gateway.audit.AuditRepository;
import sa.ncai.agent.gateway.audit.AuditStatus;
import sa.ncai.agent.gateway.auth.GatewayPrincipal;
import sa.ncai.agent.gateway.auth.KeycloakRoles;
import sa.ncai.agent.gateway.auth.TierCaps;
import sa.ncai.agent.gateway.authz.KbAccessPolicy;
import sa.ncai.agent.gateway.proxy.AgentClient;
import sa.ncai.agent.gateway.proxy.AgentRefusedException;
import sa.ncai.agent.gateway.proxy.AgentRetractRequest;
import sa.ncai.agent.gateway.proxy.AgentTurnRequest;
import sa.ncai.agent.gateway.proxy.AgentUnavailableException;
import sa.ncai.agent.gateway.proxy.ClientGoneException;
import sa.ncai.agent.gateway.proxy.RelayOutcome;
import sa.ncai.agent.gateway.proxy.SseRelay;

/**
 * One chat turn: authorize, forward to the agent, stream the agent's events back
 * unchanged, and write exactly one audit record. Audit is fail-closed (ADR-0042): a turn
 * does not start while the audit log is unreachable, and a turn whose record cannot be
 * written ends in {@code audit_unavailable} instead of its answer.
 *
 * <p>The stream is proxied on the request thread (a virtual thread), not an async
 * executor, so the trace context of the incoming request covers the agent call.
 */
@RestController
@Tag(name = "chat")
public class ChatController {

    static final String AUDIT_ACTION = "chat.turn";

    private static final Logger log = LoggerFactory.getLogger(ChatController.class);

    private final AgentClient agent;
    private final SseRelay relay;
    private final KbAccessPolicy kbAccess;
    private final ConversationRepository conversations;
    private final TierCaps tierCaps;
    private final AuditRepository audit;
    private final ErrorResponses errors;

    public ChatController(
            AgentClient agent,
            SseRelay relay,
            KbAccessPolicy kbAccess,
            ConversationRepository conversations,
            TierCaps tierCaps,
            AuditRepository audit,
            ErrorResponses errors) {
        this.agent = agent;
        this.relay = relay;
        this.kbAccess = kbAccess;
        this.conversations = conversations;
        this.tierCaps = tierCaps;
        this.audit = audit;
        this.errors = errors;
    }

    @PostMapping(
            path = "/v1/chat/turns",
            consumes = MediaType.APPLICATION_JSON_VALUE,
            produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    @Operation(
            summary = "Run one chat turn",
            description = "Streams agent events as Server-Sent Events. Event payloads are defined in "
                    + "docs/events.schema.json. Errors before the stream starts are JSON envelopes.",
            responses = {
                @ApiResponse(responseCode = "200", description = "SSE stream of agent events",
                        content = @Content(mediaType = MediaType.TEXT_EVENT_STREAM_VALUE,
                                schema = @Schema(type = "string"))),
                @ApiResponse(responseCode = "400", description = "invalid_request",
                        content = @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "401", description = "unauthorized",
                        content = @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "403", description = "kb_forbidden or conversation_forbidden",
                        content = @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "409",
                        description = "turn_in_flight: an earlier turn of this conversation is still running",
                        content = @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "502", description = "agent_unavailable",
                        content = @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "503", description = "audit_unavailable or service_busy",
                        content = @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                schema = @Schema(implementation = ApiErrorEnvelope.class)))
            })
    public void turn(
            @Valid @RequestBody ChatTurnRequest body,
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        long started = System.nanoTime();
        String requestId = RequestIdFilter.requestId(request);
        GatewayPrincipal principal = GatewayPrincipal.from(jwt);
        if (!auditReachable()) {
            errors.write(request, response, ErrorCode.AUDIT_UNAVAILABLE);
            return;
        }

        Map<String, Object> detail = new LinkedHashMap<>();
        detail.put("selected_kbs", body.selectedKbs());
        AtomicBoolean audited = new AtomicBoolean();
        AuditStatus status = AuditStatus.ERROR;
        try {
            List<String> denied = kbAccess.denied(principal, body.selectedKbs());
            if (!denied.isEmpty()) {
                detail.put("denied_kbs", denied);
                detail.put("error_code", ErrorCode.KB_FORBIDDEN.code());
                refuse(request, response, ErrorCode.KB_FORBIDDEN,
                        auditEvent(requestId, principal, body, AuditStatus.DENIED, detail, started), audited);
                return;
            }
            // A new id is bound to the caller; an id owned by another user or tenant is refused (ADR-0041).
            if (!conversations.bindOrGetOwner(body.conversationId(), principal.userId(), principal.tenantId())
                    .is(principal.userId(), principal.tenantId())) {
                detail.put("error_code", ErrorCode.CONVERSATION_FORBIDDEN.code());
                refuse(request, response, ErrorCode.CONVERSATION_FORBIDDEN,
                        auditEvent(requestId, principal, body, AuditStatus.DENIED, detail, started), audited);
                return;
            }
            // Roles are read here and only here; the agent gets the resulting ceiling (ADR-0046).
            String maxTier = tierCaps.maxTier(KeycloakRoles.realmRoles(jwt)).orElse(null);
            AgentTurnRequest turn = agentRequest(body, principal, maxTier);
            agent.streamTurn(turn, requestId, upstream -> {
                openStream(response);
                // The terminal event leaves only after this record is written (ADR-0042).
                return relay.relay(upstream, response.getOutputStream(), requestId, outcome -> {
                    audited.set(true);
                    Map<String, Object> ended = new LinkedHashMap<>(detail);
                    ended.putAll(outcomeDetail(outcome));
                    AuditStatus endStatus =
                            outcome.kind() == RelayOutcome.Kind.DONE ? AuditStatus.OK : AuditStatus.ERROR;
                    boolean recorded = append(auditEvent(requestId, principal, body, endStatus, ended, started));
                    if (!recorded && outcome.kind() == RelayOutcome.Kind.DONE) {
                        // The agent persisted the reply; the client is about to be told it was withdrawn.
                        withdrawPersistedReply(turn, outcome.turnId(), requestId);
                    }
                    return recorded;
                });
            });
        } catch (AgentRefusedException e) {
            // Refused before any stream (ADR-0109): another owner's conversation, a turn in flight (D6),
            // or no free turn lock; nothing streamed, nothing ran.
            detail.put("error_code", e.code().code());
            AuditStatus refusedStatus =
                    e.code() == ErrorCode.SERVICE_BUSY ? AuditStatus.ERROR : AuditStatus.DENIED;
            refuse(request, response, e.code(),
                    auditEvent(requestId, principal, body, refusedStatus, detail, started), audited);
        } catch (AgentUnavailableException e) {
            log.warn("{}", e.getMessage());
            detail.put("error_code", ErrorCode.AGENT_UNAVAILABLE.code());
            refuse(request, response, ErrorCode.AGENT_UNAVAILABLE,
                    auditEvent(requestId, principal, body, AuditStatus.ERROR, detail, started), audited);
        } catch (ClientGoneException e) {
            status = AuditStatus.ABORTED;
            log.info("client disconnected during turn");
        } finally {
            if (!audited.get()) {
                // Nobody is left to refuse: the client disconnected or the turn failed unexpectedly.
                append(auditEvent(requestId, principal, body, status, detail, started));
            }
        }
    }

    private static void openStream(HttpServletResponse response) {
        response.setStatus(HttpServletResponse.SC_OK);
        response.setContentType("text/event-stream;charset=UTF-8");
        response.setHeader(HttpHeaders.CACHE_CONTROL, "no-cache");
        response.setHeader("X-Accel-Buffering", "no");
        try {
            response.flushBuffer();
        } catch (IOException e) {
            throw new ClientGoneException(e);
        }
    }

    private static AgentTurnRequest agentRequest(ChatTurnRequest body, GatewayPrincipal principal, String maxTier) {
        return new AgentTurnRequest(
                body.conversationId(),
                new AgentTurnRequest.Principal(principal.userId(), principal.tenantId(), principal.permissions(),
                        maxTier, principal.displayName(), body.locale(), principal.policyRoles()),
                body.message(),
                body.attachments(),
                body.selectedKbs(),
                body.locale());
    }

    private static Map<String, Object> outcomeDetail(RelayOutcome outcome) {
        Map<String, Object> detail = new LinkedHashMap<>();
        JsonNode data = outcome.data();
        switch (outcome.kind()) {
            case DONE -> {
                if (data.hasNonNull("usage")) {
                    detail.put("usage", data.get("usage"));
                }
                if (data.hasNonNull("trace_id")) {
                    detail.put("trace_id", data.get("trace_id").asText());
                }
                detail.put("guard_events", data.path("guard_events").size());
                detail.put("guard_policies", guardPolicies(data.path("guard_events")));
            }
            case ERROR -> {
                detail.put("error_code", data.path("code").asText("unknown"));
                if (data.hasNonNull("policy_id")) {
                    detail.put("policy_id", data.get("policy_id").asText());
                }
                detail.put("guard_policies", guardPolicies(data.path("guard_events")));
            }
            case INTERRUPTED -> detail.put("error_code", ErrorCode.AGENT_STREAM_INTERRUPTED.code());
        }
        return detail;
    }

    /**
     * The audit delta of the turn's guards: every policy that did something, as
     * {@code {checkpoint, policy: id@version, action, target}}; never any matched text (A1, D5).
     */
    private static List<Map<String, Object>> guardPolicies(JsonNode events) {
        List<Map<String, Object>> policies = new ArrayList<>();
        for (JsonNode event : events) {
            String action = event.path("action").asText("");
            if (!event.hasNonNull("policy_id") || "allow".equals(action)) {
                continue;
            }
            Map<String, Object> entry = new LinkedHashMap<>();
            entry.put("checkpoint", event.path("checkpoint").asText());
            entry.put("policy", event.get("policy_id").asText() + "@" + event.path("policy_version").asText("?"));
            entry.put("action", action);
            if (event.hasNonNull("target")) {
                entry.put("target", event.get("target").asText());
            }
            policies.add(entry);
        }
        return policies;
    }

    private static AuditEvent auditEvent(
            String requestId,
            GatewayPrincipal principal,
            ChatTurnRequest body,
            AuditStatus status,
            Map<String, Object> detail,
            long started) {
        Map<String, Object> recorded = new LinkedHashMap<>(detail);
        recorded.put("latency_ms", (System.nanoTime() - started) / 1_000_000);
        return new AuditEvent(requestId, principal.userId(), principal.tenantId(), AUDIT_ACTION,
                body.conversationId(), status, recorded);
    }

    /** Audits a refusal, then answers with {@code code}, or with audit_unavailable when the record failed. */
    private void refuse(
            HttpServletRequest request,
            HttpServletResponse response,
            ErrorCode code,
            AuditEvent event,
            AtomicBoolean audited) throws IOException {
        audited.set(true);
        errors.write(request, response, append(event) ? code : ErrorCode.AUDIT_UNAVAILABLE);
    }

    private boolean auditReachable() {
        try {
            audit.check();
            return true;
        } catch (RuntimeException e) {
            log.error("audit log unreachable; refusing the turn", e);
            return false;
        }
    }

    /**
     * A finished turn whose audit record failed is withdrawn from the client by {@code retract}; its
     * reply is withdrawn from the agent's history too, so no later model call sees it (ADR-0048).
     * Best effort: the client is refused either way.
     */
    private void withdrawPersistedReply(AgentTurnRequest turn, String turnId, String requestId) {
        if (turnId == null) {
            log.error("audit write failed for request {} and its done event has no turn_id; "
                    + "the reply stays in the agent's history", requestId);
            return;
        }
        try {
            agent.retractTurn(turn.conversationId(), turnId,
                    new AgentRetractRequest(turn.principal(), ErrorCode.AUDIT_UNAVAILABLE.code()), requestId);
        } catch (RuntimeException e) {
            log.error("could not retract the reply of turn {} for request {}; it stays in the agent's history: {}",
                    turnId, requestId, e.getMessage());
        }
    }

    private boolean append(AuditEvent event) {
        try {
            audit.append(event);
            return true;
        } catch (RuntimeException e) {
            log.error("audit write failed for request {}", event.requestId(), e);
            return false;
        }
    }
}
