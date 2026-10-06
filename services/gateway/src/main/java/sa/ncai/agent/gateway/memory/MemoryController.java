package sa.ncai.agent.gateway.memory;

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
import java.util.Locale;
import java.util.Map;
import java.util.regex.Pattern;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
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
import sa.ncai.agent.gateway.proxy.AgentClient;
import sa.ncai.agent.gateway.proxy.AgentUnavailableException;

/**
 * The caller's long-term memory (README §8, §13 {@code /settings/memory}). The agent owns the data;
 * the gateway names the caller from the verified token only, so a user reaches their own items and
 * nobody else's, admins included (ADR-0059). Every request is audited like a turn: refused while
 * the audit log is unreachable, and answered only once its record is written (ADR-0042).
 */
@RestController
@Tag(name = "memory")
public class MemoryController {

    static final String LIST = "memory.list";
    static final String DELETE = "memory.delete";
    static final String SETTINGS = "memory.settings";

    private static final Pattern ITEM_ID =
            Pattern.compile("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$");

    private final AgentClient agent;
    private final AuditTrail audit;
    private final ErrorResponses errors;

    public MemoryController(AgentClient agent, AuditTrail audit, ErrorResponses errors) {
        this.agent = agent;
        this.audit = audit;
        this.errors = errors;
    }

    @GetMapping(path = "/v1/memory", produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(
            summary = "The caller's memory items and memory switch",
            responses = {
                // No content: springdoc documents the return type, the envelope of MemoryList.
                @ApiResponse(responseCode = "200", description = "The caller's memory"),
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
    public ResponseEntity<ApiEnvelope<MemoryList>> list(
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        long started = System.nanoTime();
        String requestId = RequestIdFilter.requestId(request);
        GatewayPrincipal caller = GatewayPrincipal.from(jwt);
        if (!audit.reachable()) {
            return failed(request, response, ErrorCode.AUDIT_UNAVAILABLE);
        }
        Map<String, Object> detail = new LinkedHashMap<>();
        MemoryList memory;
        try {
            memory = agent.listMemory(caller, requestId);
        } catch (AgentUnavailableException e) {
            return refuse(request, response, ErrorCode.AGENT_UNAVAILABLE, caller, LIST, AuditStatus.ERROR, detail, started);
        }
        detail.put("enabled", memory.enabled());
        detail.put("items", memory.items().size());
        if (!audit.append(event(requestId, caller, LIST, AuditStatus.OK, detail, started))) {
            return failed(request, response, ErrorCode.AUDIT_UNAVAILABLE);
        }
        return ResponseEntity.ok(ApiEnvelope.ok(requestId, memory));
    }

    @DeleteMapping(path = "/v1/memory/{itemId}")
    @Operation(
            summary = "Deletes one of the caller's memory items",
            responses = {
                @ApiResponse(responseCode = "204", description = "The item is deleted"),
                @ApiResponse(responseCode = "400", description = "invalid_request", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "401", description = "unauthorized", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "404", description = "memory_not_found, also for another user's item",
                        content = @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "502", description = "agent_unavailable", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "503", description = "audit_unavailable", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class)))
            })
    public ResponseEntity<Void> delete(
            @PathVariable("itemId") String itemId,
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        long started = System.nanoTime();
        String requestId = RequestIdFilter.requestId(request);
        GatewayPrincipal caller = GatewayPrincipal.from(jwt);
        if (!ITEM_ID.matcher(itemId).matches()) {
            // Rejected by validation, so not audited (ADR-0013).
            return failed(request, response, ErrorCode.INVALID_REQUEST);
        }
        String id = itemId.toLowerCase(Locale.ROOT);
        if (!audit.reachable()) {
            return failed(request, response, ErrorCode.AUDIT_UNAVAILABLE);
        }
        Map<String, Object> detail = new LinkedHashMap<>();
        detail.put("item_id", id);
        boolean deleted;
        try {
            deleted = agent.deleteMemory(caller, id, requestId);
        } catch (AgentUnavailableException e) {
            return refuse(request, response, ErrorCode.AGENT_UNAVAILABLE, caller, DELETE, AuditStatus.ERROR, detail, started);
        }
        if (!deleted) {
            return refuse(request, response, ErrorCode.MEMORY_NOT_FOUND, caller, DELETE, AuditStatus.DENIED, detail, started);
        }
        if (!audit.append(event(requestId, caller, DELETE, AuditStatus.OK, detail, started))) {
            return failed(request, response, ErrorCode.AUDIT_UNAVAILABLE);
        }
        return ResponseEntity.noContent().build();
    }

    @PutMapping(
            path = "/v1/memory/settings",
            consumes = MediaType.APPLICATION_JSON_VALUE,
            produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(
            summary = "Turns the caller's memory on or off",
            responses = {
                @ApiResponse(responseCode = "200", description = "The caller's memory switch as saved"),
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
    public ResponseEntity<ApiEnvelope<MemorySettings>> settings(
            @Valid @RequestBody MemorySettings body,
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        long started = System.nanoTime();
        String requestId = RequestIdFilter.requestId(request);
        GatewayPrincipal caller = GatewayPrincipal.from(jwt);
        if (!audit.reachable()) {
            return failed(request, response, ErrorCode.AUDIT_UNAVAILABLE);
        }
        Map<String, Object> detail = new LinkedHashMap<>();
        detail.put("enabled", body.enabled());
        MemorySettings updated;
        try {
            updated = agent.setMemoryEnabled(caller, body.enabled(), requestId);
        } catch (AgentUnavailableException e) {
            return refuse(request, response, ErrorCode.AGENT_UNAVAILABLE, caller, SETTINGS, AuditStatus.ERROR, detail, started);
        }
        if (!audit.append(event(requestId, caller, SETTINGS, AuditStatus.OK, detail, started))) {
            return failed(request, response, ErrorCode.AUDIT_UNAVAILABLE);
        }
        return ResponseEntity.ok(ApiEnvelope.ok(requestId, updated));
    }

    /** Writes the error envelope; the response is committed, so nothing is returned to MVC. */
    private <T> ResponseEntity<T> failed(HttpServletRequest request, HttpServletResponse response, ErrorCode code)
            throws IOException {
        errors.write(request, response, code);
        return null;
    }

    /** Audits a refusal, then answers with {@code code}, or with audit_unavailable when the record failed. */
    private <T> ResponseEntity<T> refuse(
            HttpServletRequest request,
            HttpServletResponse response,
            ErrorCode code,
            GatewayPrincipal caller,
            String action,
            AuditStatus status,
            Map<String, Object> detail,
            long started) throws IOException {
        Map<String, Object> refused = new LinkedHashMap<>(detail);
        refused.put("error_code", code.code());
        AuditEvent event = event(RequestIdFilter.requestId(request), caller, action, status, refused, started);
        return failed(request, response, audit.append(event) ? code : ErrorCode.AUDIT_UNAVAILABLE);
    }

    private static AuditEvent event(
            String requestId,
            GatewayPrincipal caller,
            String action,
            AuditStatus status,
            Map<String, Object> detail,
            long started) {
        Map<String, Object> recorded = new LinkedHashMap<>(detail);
        recorded.put("latency_ms", (System.nanoTime() - started) / 1_000_000);
        return new AuditEvent(requestId, caller.userId(), caller.tenantId(), action, null, status, recorded);
    }
}
