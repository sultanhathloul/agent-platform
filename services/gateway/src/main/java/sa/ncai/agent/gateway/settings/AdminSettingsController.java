package sa.ncai.agent.gateway.settings;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
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
import java.util.TreeSet;
import java.util.function.BiConsumer;
import java.util.regex.Pattern;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import sa.ncai.agent.gateway.api.ApiEnvelope;
import sa.ncai.agent.gateway.api.ApiError;
import sa.ncai.agent.gateway.api.ApiErrorEnvelope;
import sa.ncai.agent.gateway.api.ErrorCode;
import sa.ncai.agent.gateway.api.ErrorResponses;
import sa.ncai.agent.gateway.api.RequestIdFilter;
import sa.ncai.agent.gateway.audit.AuditEvent;
import sa.ncai.agent.gateway.audit.AuditStatus;
import sa.ncai.agent.gateway.audit.AuditTrail;
import sa.ncai.agent.gateway.auth.GatewayPrincipal;
import sa.ncai.agent.gateway.proxy.AgentClient;
import sa.ncai.agent.gateway.proxy.AgentReply;
import sa.ncai.agent.gateway.proxy.AgentUnavailableException;

/**
 * The platform settings page's API (owner decision 2026-10-22, ADR-0276 to ADR-0284). The agent
 * holds the overrides and checks each save with the timeout budget's rules; the gateway checks the
 * caller's {@code admin} role here, forwards it to the agent, which checks it too (X-User-Admin),
 * proxies the call with the caller from the verified token, and audits it fail-closed (ADR-0042):
 * refused while the audit log is unreachable, answered only once its record is written, and a
 * non-admin's attempt recorded as {@code denied}. A change's record holds every value it changed,
 * before and after, and the actor's username and display name (the policy change log precedent,
 * ADR-0123). A usage read is audited when it is the first of a page session, then at most once per
 * 10 minutes per admin (ADR-0287); a refused one always.
 */
@RestController
@Tag(name = "admin-settings")
public class AdminSettingsController {

    static final String BASE = "/v1/admin/settings";
    static final String USAGE = "/v1/admin/usage";
    private static final Map<String, ErrorCode> AGENT_CODES = Map.of(
            "settings_invalid", ErrorCode.SETTINGS_INVALID,
            "settings_conflict", ErrorCode.SETTINGS_CONFLICT,
            "settings_version_not_found", ErrorCode.SETTINGS_VERSION_NOT_FOUND,
            "invalid_request", ErrorCode.INVALID_REQUEST,
            "forbidden", ErrorCode.FORBIDDEN);
    // The page's own random id for one page load (ADR-0287): no user data.
    private static final Pattern PAGE_SESSION = Pattern.compile("[A-Za-z0-9-]{1,64}");

    private final AgentClient agent;
    private final AuditTrail audit;
    private final ErrorResponses errors;
    private final ObjectMapper mapper;
    private final UsageAuditGate usageAudit;

    public AdminSettingsController(
            AgentClient agent, AuditTrail audit, ErrorResponses errors, ObjectMapper mapper, UsageAuditGate usageAudit) {
        this.agent = agent;
        this.audit = audit;
        this.errors = errors;
        this.mapper = mapper;
        this.usageAudit = usageAudit;
    }

    @GetMapping(path = BASE, produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(summary = "Every editable setting (default, value in force, range), the read-only ones and the "
            + "timeout tree", responses = {
        @ApiResponse(responseCode = "200", description = "The settings"),
        @ApiResponse(responseCode = "403", description = "forbidden: not an admin", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "502", description = "agent_unavailable", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "503", description = "audit_unavailable", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class)))
    })
    public ResponseEntity<ApiEnvelope<PlatformSettingsView>> getSettings(
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        return proxy(jwt, request, response, "settings.get", HttpMethod.GET, BASE, null, PlatformSettingsView.class,
                200, new LinkedHashMap<>(), (detail, view) -> detail.put("version", view.version()));
    }

    @PostMapping(path = BASE + "/preview", consumes = MediaType.APPLICATION_JSON_VALUE,
            produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(summary = "What a draft would produce, and why it cannot be saved if it cannot; nothing is written",
            responses = {
        @ApiResponse(responseCode = "200", description = "The values, the timeout tree and the problems"),
        @ApiResponse(responseCode = "400", description = "invalid_request", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "403", description = "forbidden: not an admin", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class)))
    })
    public ResponseEntity<ApiEnvelope<SettingsPreview>> previewSettings(
            @Valid @RequestBody SettingsDraft body,
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        Map<String, Object> detail = new LinkedHashMap<>();
        detail.put("keys", new TreeSet<>(body.overrides().keySet()));
        return proxy(jwt, request, response, "settings.preview", HttpMethod.POST, BASE + "/preview", body,
                SettingsPreview.class, 200, detail, (d, preview) -> {
                    d.put("valid", preview.valid());
                    d.put("problems", preview.problems().stream().map(SettingsProblem::rule).toList());
                });
    }

    @PostMapping(path = BASE, consumes = MediaType.APPLICATION_JSON_VALUE, produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(summary = "Saves a draft as the next version; applies to requests that start after it", responses = {
        @ApiResponse(responseCode = "201", description = "The saved version, what it changed, the settings now"),
        @ApiResponse(responseCode = "400", description = "settings_invalid: the conflicts in plain words, each "
                + "rule's id in details", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "403", description = "forbidden: not an admin", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "409", description = "settings_conflict: another save came first", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class)))
    })
    public ResponseEntity<ApiEnvelope<SettingsSaved>> saveSettings(
            @Valid @RequestBody SettingsDraft body,
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        Map<String, Object> detail = new LinkedHashMap<>();
        detail.put("keys", new TreeSet<>(body.overrides().keySet()));
        return proxy(jwt, request, response, "settings.save", HttpMethod.POST, BASE, body, SettingsSaved.class, 201,
                detail, AdminSettingsController::describeSaved);
    }

    @PostMapping(path = BASE + "/revert", consumes = MediaType.APPLICATION_JSON_VALUE,
            produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(summary = "Brings an earlier version's overrides back as a new version", responses = {
        @ApiResponse(responseCode = "201", description = "The new version, what it changed, the settings now"),
        @ApiResponse(responseCode = "400", description = "settings_invalid", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "403", description = "forbidden: not an admin", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "404", description = "settings_version_not_found", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "409", description = "settings_conflict", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class)))
    })
    public ResponseEntity<ApiEnvelope<SettingsSaved>> revertSettings(
            @Valid @RequestBody SettingsRevert body,
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        Map<String, Object> detail = new LinkedHashMap<>();
        detail.put("revert_to", body.version());
        return proxy(jwt, request, response, "settings.revert", HttpMethod.POST, BASE + "/revert", body,
                SettingsSaved.class, 201, detail, AdminSettingsController::describeSaved);
    }

    @GetMapping(path = BASE + "/versions", produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(summary = "The versions of the overrides, newest first, with who made each", responses = {
        @ApiResponse(responseCode = "200", description = "The versions"),
        @ApiResponse(responseCode = "400", description = "invalid_request", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "403", description = "forbidden: not an admin", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class)))
    })
    public ResponseEntity<ApiEnvelope<SettingsVersions>> settingsVersions(
            @RequestParam(name = "limit", defaultValue = "50") int limit,
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        if (limit < 1 || limit > 200) {
            return failed(request, response, ErrorCode.INVALID_REQUEST, null);
        }
        Map<String, Object> detail = new LinkedHashMap<>();
        detail.put("limit", limit);
        return proxy(jwt, request, response, "settings.versions", HttpMethod.GET, BASE + "/versions?limit=" + limit,
                null, SettingsVersions.class, 200, detail, (d, list) -> d.put("versions", list.versions().size()));
    }

    @GetMapping(path = USAGE, produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(summary = "What is in use now: turns, MCP slots and breakers, background registrations; counts only",
            responses = {
        @ApiResponse(responseCode = "200", description = "The counts"),
        @ApiResponse(responseCode = "400", description = "invalid_request: a malformed page_session", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "403", description = "forbidden: not an admin", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class)))
    })
    public ResponseEntity<ApiEnvelope<PlatformUsage>> platformUsage(
            @Parameter(description = "The page's random id for one page load: its first read is audited, then at "
                    + "most one read per 10 minutes per admin (ADR-0287)")
            @RequestParam(name = "page_session", required = false) String pageSession,
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        if (pageSession != null && !PAGE_SESSION.matcher(pageSession).matches()) {
            return failed(request, response, ErrorCode.INVALID_REQUEST, null);
        }
        Map<String, Object> detail = new LinkedHashMap<>();
        if (pageSession != null) {
            detail.put("page_session", pageSession);
        }
        return proxy(jwt, request, response, "settings.usage", HttpMethod.GET, USAGE, null, PlatformUsage.class, 200,
                detail, (d, usage) -> d.put("turns_in_flight", usage.turns().inFlight()), true, pageSession);
    }

    /** Audit check, admin check, agent call, audit record, in that order (ADR-0042). */
    private <T> ResponseEntity<ApiEnvelope<T>> proxy(
            Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response,
            String action,
            HttpMethod method,
            String uri,
            Object body,
            Class<T> type,
            int okStatus,
            Map<String, Object> detail,
            BiConsumer<Map<String, Object>, T> describe) throws IOException {
        return proxy(jwt, request, response, action, method, uri, body, type, okStatus, detail, describe, false, null);
    }

    /** As above; a {@code gated} read's OK record is written only when {@link UsageAuditGate} says it is due. */
    private <T> ResponseEntity<ApiEnvelope<T>> proxy(
            Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response,
            String action,
            HttpMethod method,
            String uri,
            Object body,
            Class<T> type,
            int okStatus,
            Map<String, Object> detail,
            BiConsumer<Map<String, Object>, T> describe,
            boolean gated,
            String pageSession) throws IOException {
        long started = System.nanoTime();
        String requestId = RequestIdFilter.requestId(request);
        GatewayPrincipal caller = GatewayPrincipal.from(jwt);
        // Who acted, by name as the token said at the time (ADR-0123), on every record.
        detail.put("actor_username", caller.username());
        detail.put("actor_name", caller.displayName());
        if (!audit.reachable()) {
            return failed(request, response, ErrorCode.AUDIT_UNAVAILABLE, null);
        }
        if (!caller.admin()) {
            return refuse(request, response, ErrorCode.FORBIDDEN, null, caller, action, AuditStatus.DENIED, detail,
                    started);
        }
        AgentReply reply;
        try {
            reply = agent.exchangeAdmin(method, uri, body, caller, requestId);
        } catch (AgentUnavailableException e) {
            return refuse(request, response, ErrorCode.AGENT_UNAVAILABLE, null, caller, action, AuditStatus.ERROR,
                    detail, started);
        }
        if (!reply.ok()) {
            JsonNode error = readError(reply);
            ErrorCode code = AGENT_CODES.getOrDefault(error.path("code").asText(""), ErrorCode.AGENT_UNAVAILABLE);
            List<String> details = new ArrayList<>();
            error.path("details").forEach(item -> details.add(item.asText()));
            ApiError relayed = null;
            if (code == ErrorCode.SETTINGS_INVALID && error.hasNonNull("message_ar") && error.hasNonNull("message_en")) {
                // The agent's sentences name each conflict in plain words (ADR-0278).
                relayed = new ApiError(code.code(), error.path("message_ar").asText(), error.path("message_en").asText())
                        .withDetails(details);
            }
            detail.put("details", details);
            return refuse(request, response, code, relayed, caller, action, AuditStatus.ERROR, detail, started,
                    details);
        }
        T value = mapper.readValue(reply.body(), type);
        describe.accept(detail, value);
        UsageAuditGate.Claim claim = null;
        if (gated) {
            claim = usageAudit.claim(caller.userId(), pageSession);
            if (claim == null) {
                return ResponseEntity.status(okStatus).body(ApiEnvelope.ok(requestId, value));
            }
        }
        if (!audit.append(event(requestId, caller, action, AuditStatus.OK, detail, started))) {
            if (claim != null) {
                usageAudit.release(claim);
            }
            return failed(request, response, ErrorCode.AUDIT_UNAVAILABLE, null);
        }
        return ResponseEntity.status(okStatus).body(ApiEnvelope.ok(requestId, value));
    }

    /** A saved version's record: its number, its kind, and each value it changed, before and after. */
    private static void describeSaved(Map<String, Object> detail, SettingsSaved saved) {
        detail.put("version", saved.version());
        detail.put("change", saved.change());
        if (saved.revertedTo() != null) {
            detail.put("reverted_to", saved.revertedTo());
        }
        List<Map<String, Object>> changes = new ArrayList<>();
        for (SettingChange change : saved.changes()) {
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("key", change.key());
            item.put("before", change.before());
            item.put("after", change.after());
            changes.add(item);
        }
        detail.put("changes", changes);
    }

    private JsonNode readError(AgentReply reply) {
        try {
            return mapper.readTree(reply.body()).path("error");
        } catch (IOException e) {
            return mapper.missingNode();
        }
    }

    private <T> ResponseEntity<T> failed(
            HttpServletRequest request, HttpServletResponse response, ErrorCode code, List<String> details)
            throws IOException {
        errors.write(request, response, code, code.status(), details);
        return null;
    }

    private <T> ResponseEntity<T> refuse(
            HttpServletRequest request,
            HttpServletResponse response,
            ErrorCode code,
            ApiError relayed,
            GatewayPrincipal caller,
            String action,
            AuditStatus status,
            Map<String, Object> detail,
            long started) throws IOException {
        return refuse(request, response, code, relayed, caller, action, status, detail, started, null);
    }

    /** Audits a refusal, then answers with it, or with audit_unavailable when the record failed. */
    private <T> ResponseEntity<T> refuse(
            HttpServletRequest request,
            HttpServletResponse response,
            ErrorCode code,
            ApiError relayed,
            GatewayPrincipal caller,
            String action,
            AuditStatus status,
            Map<String, Object> detail,
            long started,
            List<String> details) throws IOException {
        Map<String, Object> refused = new LinkedHashMap<>(detail);
        refused.put("error_code", code.code());
        boolean recorded = audit.append(event(RequestIdFilter.requestId(request), caller, action, status, refused, started));
        if (!recorded) {
            return failed(request, response, ErrorCode.AUDIT_UNAVAILABLE, null);
        }
        if (relayed != null) {
            errors.write(request, response, code.status(), relayed);
            return null;
        }
        return failed(request, response, code, details);
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
