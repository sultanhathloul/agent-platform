package sa.ncai.agent.gateway.admin;

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
import java.util.function.BiConsumer;
import java.util.regex.Pattern;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
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
import sa.ncai.agent.gateway.auth.KeycloakAdminClient;
import sa.ncai.agent.gateway.proxy.AgentClient;
import sa.ncai.agent.gateway.proxy.AgentReply;
import sa.ncai.agent.gateway.proxy.AgentUnavailableException;

/**
 * Guard policy administration (README §10.4, phase 03 C3). The agent owns the policies (I1); the
 * gateway checks the caller's {@code admin} role here and only here (the agent stays role-blind,
 * ADR-0046), proxies the call with the caller from the verified token, and audits it fail-closed
 * (ADR-0042): refused while the audit log is unreachable, answered only once its record is written,
 * and a non-admin's attempt is recorded as {@code denied}. A tested text never enters the audit log.
 */
@RestController
@Tag(name = "admin-policies")
public class AdminPolicyController {

    static final String BASE = "/v1/admin/policies";
    private static final Pattern POLICY_ID = Pattern.compile("^[a-z0-9][a-z0-9-]{1,63}$");
    private static final Map<String, ErrorCode> AGENT_CODES = Map.of(
            "policy_not_found", ErrorCode.POLICY_NOT_FOUND,
            "policy_exists", ErrorCode.POLICY_EXISTS,
            "policy_invalid", ErrorCode.POLICY_INVALID,
            "invalid_request", ErrorCode.INVALID_REQUEST);

    private final AgentClient agent;
    private final AuditTrail audit;
    private final ErrorResponses errors;
    private final ObjectMapper mapper;
    private final KeycloakAdminClient keycloak;

    public AdminPolicyController(
            AgentClient agent,
            AuditTrail audit,
            ErrorResponses errors,
            ObjectMapper mapper,
            KeycloakAdminClient keycloak) {
        this.agent = agent;
        this.audit = audit;
        this.errors = errors;
        this.mapper = mapper;
        this.keycloak = keycloak;
    }

    @GetMapping(path = BASE, produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(summary = "Every policy's current version and switch", responses = {
        @ApiResponse(responseCode = "200", description = "The policies"),
        @ApiResponse(responseCode = "401", description = "unauthorized", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "403", description = "forbidden: not an admin", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "502", description = "agent_unavailable", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "503", description = "audit_unavailable", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class)))
    })
    public ResponseEntity<ApiEnvelope<PolicyList>> listPolicies(
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        return proxy(jwt, request, response, "policy.list", HttpMethod.GET, BASE, null, PolicyList.class, 200,
                new LinkedHashMap<>(), (detail, list) -> detail.put("policies", list.policies().size()));
    }

    @GetMapping(path = BASE + "/{policyId}", produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(summary = "A policy's current version and its version history", responses = {
        @ApiResponse(responseCode = "200", description = "The policy"),
        @ApiResponse(responseCode = "400", description = "invalid_request", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "403", description = "forbidden: not an admin", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "404", description = "policy_not_found", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class)))
    })
    public ResponseEntity<ApiEnvelope<PolicyDetail>> getPolicy(
            @PathVariable("policyId") String policyId,
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        if (!POLICY_ID.matcher(policyId).matches()) {
            return failed(request, response, ErrorCode.INVALID_REQUEST, null);
        }
        Map<String, Object> detail = withPolicy(policyId);
        return proxy(jwt, request, response, "policy.get", HttpMethod.GET, BASE + "/" + policyId, null,
                PolicyDetail.class, 200, detail, (d, found) -> d.put("version", found.current().policy().version()));
    }

    @PostMapping(path = BASE, consumes = MediaType.APPLICATION_JSON_VALUE, produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(summary = "Creates a policy (version 1)", responses = {
        @ApiResponse(responseCode = "201", description = "The created policy"),
        @ApiResponse(responseCode = "400", description = "policy_invalid, with details", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "403", description = "forbidden: not an admin", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "409", description = "policy_exists", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class)))
    })
    public ResponseEntity<ApiEnvelope<PolicyView>> createPolicy(
            @RequestBody PolicyDocument body,
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        Map<String, Object> detail = describeDocument(body);
        return proxy(jwt, request, response, "policy.create", HttpMethod.POST, BASE, body, PolicyView.class, 201,
                detail, (d, created) -> describeSaved(d, created));
    }

    @PostMapping(path = BASE + "/{policyId}/versions", consumes = MediaType.APPLICATION_JSON_VALUE,
            produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(summary = "Saves an edit as the next, immutable version and makes it current", responses = {
        @ApiResponse(responseCode = "201", description = "The new current version"),
        @ApiResponse(responseCode = "400", description = "policy_invalid, with details", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "403", description = "forbidden: not an admin", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "404", description = "policy_not_found", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class)))
    })
    public ResponseEntity<ApiEnvelope<PolicyView>> addPolicyVersion(
            @PathVariable("policyId") String policyId,
            @RequestBody PolicyDocument body,
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        if (!POLICY_ID.matcher(policyId).matches()) {
            return failed(request, response, ErrorCode.INVALID_REQUEST, null);
        }
        Map<String, Object> detail = describeDocument(body);
        detail.put("policy_id", policyId);
        return proxy(jwt, request, response, "policy.version", HttpMethod.POST, BASE + "/" + policyId + "/versions",
                body, PolicyView.class, 201, detail, AdminPolicyController::describeSaved);
    }

    @PostMapping(path = BASE + "/overrides", consumes = MediaType.APPLICATION_JSON_VALUE,
            produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(summary = "What a draft would override if saved now (ADR-0105)", responses = {
        @ApiResponse(responseCode = "200", description = "The overridden policies and the exception flag"),
        @ApiResponse(responseCode = "400", description = "policy_invalid, with details", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "403", description = "forbidden: not an admin", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class)))
    })
    public ResponseEntity<ApiEnvelope<PolicyOverrides>> previewOverrides(
            @RequestBody PolicyDocument body,
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        return proxy(jwt, request, response, "policy.preview", HttpMethod.POST, BASE + "/overrides", body,
                PolicyOverrides.class, 200, describeDocument(body), (d, found) -> {
                    d.put("overrides", found.overrides());
                    d.put("exception", found.exception());
                });
    }

    @GetMapping(path = "/v1/admin/policy-roles", produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(summary = "The realm's policy:* roles, which role-scoped policies name (ADR-0107)", responses = {
        @ApiResponse(responseCode = "200", description = "The policy roles"),
        @ApiResponse(responseCode = "403", description = "forbidden: not an admin", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "502", description = "identity_unavailable", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "503", description = "audit_unavailable", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class)))
    })
    public ResponseEntity<ApiEnvelope<PolicyRoles>> listPolicyRoles(
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        long started = System.nanoTime();
        String requestId = RequestIdFilter.requestId(request);
        GatewayPrincipal caller = GatewayPrincipal.from(jwt);
        String action = "policy.roles";
        Map<String, Object> detail = new LinkedHashMap<>();
        if (!audit.reachable()) {
            return failed(request, response, ErrorCode.AUDIT_UNAVAILABLE, null);
        }
        if (!caller.admin()) {
            return refuse(request, response, ErrorCode.FORBIDDEN, caller, action, AuditStatus.DENIED, detail, started,
                    null);
        }
        List<String> roles;
        try {
            roles = keycloak.policyRoles();
        } catch (KeycloakAdminClient.IdentityUnavailableException e) {
            return refuse(request, response, ErrorCode.IDENTITY_UNAVAILABLE, caller, action, AuditStatus.ERROR, detail,
                    started, null);
        }
        detail.put("roles", roles.size());
        if (!audit.append(event(requestId, caller, action, AuditStatus.OK, detail, started))) {
            return failed(request, response, ErrorCode.AUDIT_UNAVAILABLE, null);
        }
        return ResponseEntity.ok(ApiEnvelope.ok(requestId, new PolicyRoles(roles)));
    }

    @PutMapping(path = BASE + "/{policyId}/enabled", consumes = MediaType.APPLICATION_JSON_VALUE,
            produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(summary = "Switches a policy on or off (no new version)", responses = {
        @ApiResponse(responseCode = "200", description = "The policy as switched"),
        @ApiResponse(responseCode = "400", description = "invalid_request", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "403", description = "forbidden: not an admin", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "404", description = "policy_not_found", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class)))
    })
    public ResponseEntity<ApiEnvelope<PolicyView>> setPolicyEnabled(
            @PathVariable("policyId") String policyId,
            @Valid @RequestBody PolicyEnabled body,
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        if (!POLICY_ID.matcher(policyId).matches()) {
            return failed(request, response, ErrorCode.INVALID_REQUEST, null);
        }
        Map<String, Object> detail = withPolicy(policyId);
        detail.put("enabled", body.enabled());
        return proxy(jwt, request, response, "policy.enable", HttpMethod.PUT, BASE + "/" + policyId + "/enabled",
                body, PolicyView.class, 200, detail, (d, view) -> d.put("version", view.policy().version()));
    }

    @PostMapping(path = BASE + "/test", consumes = MediaType.APPLICATION_JSON_VALUE,
            produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(summary = "Evaluates one policy against sample text; nothing is recorded", responses = {
        @ApiResponse(responseCode = "200", description = "The CheckResult and the engine's decision"),
        @ApiResponse(responseCode = "400", description = "invalid_request or policy_invalid", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "403", description = "forbidden: not an admin", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "404", description = "policy_not_found", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class)))
    })
    public ResponseEntity<ApiEnvelope<PolicyTestResult>> testPolicy(
            @Valid @RequestBody PolicyTestRequest body,
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        Map<String, Object> detail = new LinkedHashMap<>();
        detail.put("policy_id", body.policyId() != null ? body.policyId()
                : body.policy() != null ? body.policy().id() + " (draft)" : null);
        detail.put("text_chars", body.text().length());  // the text itself never enters the audit log
        return proxy(jwt, request, response, "policy.test", HttpMethod.POST, BASE + "/test", body,
                PolicyTestResult.class, 200, detail, (d, result) -> {
                    d.put("policy_ref", result.policyRef());
                    d.put("checkpoint", result.checkpoint());
                    d.put("action", result.action());
                    d.put("matched", result.matched());
                });
    }

    @GetMapping(path = BASE + "/export", produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(summary = "Every policy's current version as one importable YAML document", responses = {
        @ApiResponse(responseCode = "200", description = "The YAML document"),
        @ApiResponse(responseCode = "403", description = "forbidden: not an admin", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class)))
    })
    public ResponseEntity<ApiEnvelope<PolicyYaml>> exportPolicies(
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        return proxy(jwt, request, response, "policy.export", HttpMethod.GET, BASE + "/export", null,
                PolicyYaml.class, 200, new LinkedHashMap<>(), (d, document) -> d.put("chars", document.yaml().length()));
    }

    @PostMapping(path = BASE + "/import", consumes = MediaType.APPLICATION_JSON_VALUE,
            produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(summary = "Imports a YAML document, all or nothing", responses = {
        @ApiResponse(responseCode = "200", description = "What the import did"),
        @ApiResponse(responseCode = "400", description = "policy_invalid, with details", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "403", description = "forbidden: not an admin", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class)))
    })
    public ResponseEntity<ApiEnvelope<PolicyImportReport>> importPolicies(
            @Valid @RequestBody PolicyYaml body,
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        return proxy(jwt, request, response, "policy.import", HttpMethod.POST, BASE + "/import", body,
                PolicyImportReport.class, 200, new LinkedHashMap<>(), (d, report) -> {
                    d.put("created", report.created());
                    d.put("versioned", report.versioned());
                    d.put("switched", report.switched());
                    d.put("unchanged", report.unchanged().size());
                });
    }

    @GetMapping(path = BASE + "/metrics", produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(summary = "Matches per day per policy, from guard events", responses = {
        @ApiResponse(responseCode = "200", description = "The counts"),
        @ApiResponse(responseCode = "400", description = "invalid_request", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "403", description = "forbidden: not an admin", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class)))
    })
    public ResponseEntity<ApiEnvelope<PolicyMetrics>> policyMetrics(
            @RequestParam(name = "days", defaultValue = "14") int days,
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        if (days < 1 || days > 90) {
            return failed(request, response, ErrorCode.INVALID_REQUEST, null);
        }
        Map<String, Object> detail = new LinkedHashMap<>();
        detail.put("days", days);
        return proxy(jwt, request, response, "policy.metrics", HttpMethod.GET, BASE + "/metrics?days=" + days, null,
                PolicyMetrics.class, 200, detail, (d, metrics) -> d.put("rows", metrics.rows().size()));
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
        long started = System.nanoTime();
        String requestId = RequestIdFilter.requestId(request);
        GatewayPrincipal caller = GatewayPrincipal.from(jwt);
        if (!audit.reachable()) {
            return failed(request, response, ErrorCode.AUDIT_UNAVAILABLE, null);
        }
        if (!caller.admin()) {
            return refuse(request, response, ErrorCode.FORBIDDEN, caller, action, AuditStatus.DENIED, detail, started,
                    null);
        }
        AgentReply reply;
        try {
            reply = agent.exchangeAdmin(method, uri, body, caller, requestId);
        } catch (AgentUnavailableException e) {
            return refuse(request, response, ErrorCode.AGENT_UNAVAILABLE, caller, action, AuditStatus.ERROR, detail,
                    started, null);
        }
        if (!reply.ok()) {
            JsonNode error = readError(reply);
            ErrorCode code = AGENT_CODES.getOrDefault(error.path("code").asText(""), ErrorCode.AGENT_UNAVAILABLE);
            List<String> details = new ArrayList<>();
            error.path("details").forEach(item -> details.add(item.asText()));
            return refuse(request, response, code, caller, action, AuditStatus.ERROR, detail, started, details);
        }
        T value = mapper.readValue(reply.body(), type);
        describe.accept(detail, value);
        if (!audit.append(event(requestId, caller, action, AuditStatus.OK, detail, started))) {
            return failed(request, response, ErrorCode.AUDIT_UNAVAILABLE, null);
        }
        return ResponseEntity.status(okStatus).body(ApiEnvelope.ok(requestId, value));
    }

    private JsonNode readError(AgentReply reply) {
        try {
            return mapper.readTree(reply.body()).path("error");
        } catch (IOException e) {
            return mapper.missingNode();
        }
    }

    /** A saved version's audit detail: its version and, for an exception, what it weakens (ADR-0105). */
    private static void describeSaved(Map<String, Object> detail, PolicyView saved) {
        detail.put("version", saved.policy().version());
        detail.put("exception", Boolean.TRUE.equals(saved.exception()));
        detail.put("overrides", saved.overrides() == null ? List.of() : saved.overrides());
    }

    private static Map<String, Object> withPolicy(String policyId) {
        Map<String, Object> detail = new LinkedHashMap<>();
        detail.put("policy_id", policyId);
        return detail;
    }

    private static Map<String, Object> describeDocument(PolicyDocument body) {
        Map<String, Object> detail = new LinkedHashMap<>();
        detail.put("policy_id", body.id());
        detail.put("scope", body.scope());
        detail.put("checker", body.checker());
        detail.put("action", body.action());
        return detail;
    }

    /** Writes the error envelope; the response is committed, so nothing is returned to MVC. */
    private <T> ResponseEntity<T> failed(
            HttpServletRequest request, HttpServletResponse response, ErrorCode code, List<String> details)
            throws IOException {
        errors.write(request, response, code, code.status(), details);
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
            long started,
            List<String> details) throws IOException {
        Map<String, Object> refused = new LinkedHashMap<>(detail);
        refused.put("error_code", code.code());
        AuditEvent event = event(RequestIdFilter.requestId(request), caller, action, status, refused, started);
        boolean recorded = audit.append(event);
        return failed(request, response, recorded ? code : ErrorCode.AUDIT_UNAVAILABLE, recorded ? details : null);
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
