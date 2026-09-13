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
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
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
import sa.ncai.agent.gateway.authz.KbAccessPolicy;
import sa.ncai.agent.gateway.proxy.AgentClient;
import sa.ncai.agent.gateway.proxy.AgentTurnRequest;
import sa.ncai.agent.gateway.proxy.AgentUnavailableException;
import sa.ncai.agent.gateway.proxy.ClientGoneException;
import sa.ncai.agent.gateway.proxy.RelayOutcome;
import sa.ncai.agent.gateway.proxy.SseRelay;

/**
 * One chat turn: authorize, forward to the agent, stream the agent's events back
 * unchanged, and write exactly one audit record.
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
    private final AuditRepository audit;
    private final ErrorResponses errors;

    public ChatController(
            AgentClient agent, SseRelay relay, KbAccessPolicy kbAccess, AuditRepository audit, ErrorResponses errors) {
        this.agent = agent;
        this.relay = relay;
        this.kbAccess = kbAccess;
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
                @ApiResponse(responseCode = "403", description = "kb_forbidden",
                        content = @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "502", description = "agent_unavailable",
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
        Map<String, Object> detail = new LinkedHashMap<>();
        detail.put("selected_kbs", body.selectedKbs());
        AuditStatus status = AuditStatus.ERROR;
        try {
            List<String> denied = kbAccess.denied(principal, body.selectedKbs());
            if (!denied.isEmpty()) {
                status = AuditStatus.DENIED;
                detail.put("denied_kbs", denied);
                detail.put("error_code", ErrorCode.KB_FORBIDDEN.code());
                errors.write(request, response, ErrorCode.KB_FORBIDDEN);
                return;
            }
            RelayOutcome outcome = agent.streamTurn(agentRequest(body, principal), requestId, upstream -> {
                openStream(response);
                return relay.relay(upstream, response.getOutputStream(), requestId);
            });
            status = outcome.kind() == RelayOutcome.Kind.DONE ? AuditStatus.OK : AuditStatus.ERROR;
            detail.putAll(outcomeDetail(outcome));
        } catch (AgentUnavailableException e) {
            log.warn("{}", e.getMessage());
            detail.put("error_code", ErrorCode.AGENT_UNAVAILABLE.code());
            errors.write(request, response, ErrorCode.AGENT_UNAVAILABLE);
        } catch (ClientGoneException e) {
            status = AuditStatus.ABORTED;
            log.info("client disconnected during turn");
        } finally {
            detail.put("latency_ms", (System.nanoTime() - started) / 1_000_000);
            append(new AuditEvent(requestId, principal.userId(), principal.tenantId(), AUDIT_ACTION,
                    body.conversationId(), status, detail));
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

    private static AgentTurnRequest agentRequest(ChatTurnRequest body, GatewayPrincipal principal) {
        return new AgentTurnRequest(
                body.conversationId(),
                new AgentTurnRequest.Principal(principal.userId(), principal.tenantId(), principal.permissions(),
                        principal.roles(), principal.displayName(), body.locale()),
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
            }
            case ERROR -> detail.put("error_code", data.path("code").asText("unknown"));
            case INTERRUPTED -> detail.put("error_code", ErrorCode.AGENT_STREAM_INTERRUPTED.code());
        }
        return detail;
    }

    private void append(AuditEvent event) {
        try {
            audit.append(event);
        } catch (RuntimeException e) {
            log.error("audit write failed for request {}", event.requestId(), e);
        }
    }
}
