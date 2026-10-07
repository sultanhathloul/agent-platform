package sa.ncai.agent.gateway.admin;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import sa.ncai.agent.gateway.GatewayTestSupport;
import sa.ncai.agent.gateway.InMemoryAuditRepository;
import sa.ncai.agent.gateway.audit.AuditEvent;
import sa.ncai.agent.gateway.audit.AuditStatus;
import sa.ncai.agent.gateway.auth.KeycloakAdminClient;

/** Guard policy administration: admin-only, proxied with the caller from the token, audited fail-closed. */
class AdminPolicyProxyTest extends GatewayTestSupport {

    private static final String POLICY = """
            {"id":"pii-redact-output","version":2,"name":"Redact PII","description":"","enabled":true,\
            "scope":"global","checkpoints":["DATA"],"checker":"presidio",\
            "config":{"entities":["SA_MOBILE"],"targets":["answer","summary"]},"action":"redact",\
            "severity":"high","message_ar":null,"message_en":null,"on_error":null,"group":"pii-output"}""";
    private static final String VIEW = "{\"policy\":" + POLICY + ",\"created_at\":\"2026-10-06T10:00:00Z\","
            + "\"created_by\":\"seed\",\"updated_at\":\"2026-10-06T11:00:00Z\",\"updated_by\":\"admin-1\"}";
    private static final String DRAFT = """
            {"id":"keyword-test","name":"Keyword test","scope":"tenant:moe","checkpoints":["INPUT"],\
            "checker":"keyword_list","config":{"keywords":["كلمة"]},"action":"block",\
            "message_ar":"محجوب","message_en":"Blocked"}""";

    @Autowired
    ObjectMapper mapper;

    private static String adminToken() {
        return token(claims -> claims.claim("realm_access",
                Map.of("roles", List.of("user", "admin", "cap:general"))));
    }

    @Test
    void aNonAdminIsRefusedAuditedAndTheAgentNeverCalled() throws Exception {
        int before = AGENT.getRequestCount();
        mvc.perform(get("/v1/admin/policies").header(HttpHeaders.AUTHORIZATION, bearer(token())))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error.code").value("forbidden"));
        assertThat(AGENT.getRequestCount()).isEqualTo(before);
        AuditEvent event = singleAudit();
        assertThat(event.action()).isEqualTo("policy.list");
        assertThat(event.status()).isEqualTo(AuditStatus.DENIED);
        assertThat(event.detail()).containsEntry("error_code", "forbidden");
    }

    @Test
    void anAdminListsPoliciesWithTheCallerForwarded() throws Exception {
        AGENT.enqueue(json(200, "{\"policies\":[" + VIEW + "]}"));
        mvc.perform(get("/v1/admin/policies").header(HttpHeaders.AUTHORIZATION, bearer(adminToken())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.request_id").isNotEmpty())
                .andExpect(jsonPath("$.data.policies[0].policy.id").value("pii-redact-output"))
                .andExpect(jsonPath("$.data.policies[0].policy.version").value(2))
                .andExpect(jsonPath("$.data.policies[0].policy.config.targets[1]").value("summary"))
                .andExpect(jsonPath("$.data.policies[0].updated_by").value("admin-1"));
        RecordedRequest upstream = AGENT.takeRequest(5, TimeUnit.SECONDS);
        assertThat(upstream.getPath()).isEqualTo("/v1/admin/policies");
        assertThat(upstream.getHeader("X-Internal-Token")).isEqualTo(INTERNAL_TOKEN);
        assertThat(upstream.getHeader("X-User-Id")).isEqualTo("user-123");
        assertThat(upstream.getHeader("X-Tenant-Id")).isEqualTo("tenant-a");
        AuditEvent event = singleAudit();
        assertThat(event.status()).isEqualTo(AuditStatus.OK);
        assertThat(event.detail()).containsEntry("policies", 1);
    }

    @Test
    void createForwardsTheDraftAndAuditsItsIdAndVersion() throws Exception {
        AGENT.enqueue(json(201, VIEW.replace("\"version\":2", "\"version\":1").replace("pii-redact-output", "keyword-test")));
        mvc.perform(post("/v1/admin/policies")
                        .header(HttpHeaders.AUTHORIZATION, bearer(adminToken()))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(DRAFT))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.data.policy.version").value(1));
        RecordedRequest upstream = AGENT.takeRequest(5, TimeUnit.SECONDS);
        assertThat(upstream.getMethod()).isEqualTo("POST");
        JsonNode sent = mapper.readTree(upstream.getBody().readUtf8());
        assertThat(sent.path("message_ar").asText()).isEqualTo("محجوب");
        assertThat(sent.path("config").path("keywords").get(0).asText()).isEqualTo("كلمة");
        AuditEvent event = singleAudit();
        assertThat(event.action()).isEqualTo("policy.create");
        assertThat(event.detail()).containsEntry("policy_id", "keyword-test").containsEntry("version", 1);
    }

    @Test
    void theAgentsValidationErrorsReachTheAdminWithDetails() throws Exception {
        AGENT.enqueue(json(400, """
                {"request_id":"r","error":{"code":"policy_invalid","message_ar":"x","message_en":"x",\
                "details":["scope: Value error, scope 'role:viewer': role scopes are reserved"]}}"""));
        mvc.perform(post("/v1/admin/policies")
                        .header(HttpHeaders.AUTHORIZATION, bearer(adminToken()))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(DRAFT.replace("tenant:moe", "role:viewer")))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error.code").value("policy_invalid"))
                .andExpect(jsonPath("$.error.message_ar").value("السياسة غير صالحة. راجع التفاصيل المرفقة."))
                .andExpect(jsonPath("$.error.details[0]").value(
                        "scope: Value error, scope 'role:viewer': role scopes are reserved"));
        AuditEvent event = singleAudit();
        assertThat(event.status()).isEqualTo(AuditStatus.ERROR);
        assertThat(event.detail()).containsEntry("error_code", "policy_invalid");
    }

    @Test
    void missingAndDuplicatePoliciesKeepTheirCodes() throws Exception {
        AGENT.enqueue(json(404, "{\"request_id\":\"r\",\"error\":{\"code\":\"policy_not_found\",\"message_ar\":\"x\",\"message_en\":\"x\"}}"));
        mvc.perform(get("/v1/admin/policies/no-such-policy").header(HttpHeaders.AUTHORIZATION, bearer(adminToken())))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error.code").value("policy_not_found"));
        AGENT.enqueue(json(409, "{\"request_id\":\"r\",\"error\":{\"code\":\"policy_exists\",\"message_ar\":\"x\",\"message_en\":\"x\"}}"));
        mvc.perform(post("/v1/admin/policies")
                        .header(HttpHeaders.AUTHORIZATION, bearer(adminToken()))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(DRAFT))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.error.code").value("policy_exists"));
    }

    @Test
    void theTestedTextNeverEntersTheAuditLog() throws Exception {
        AGENT.enqueue(json(200, """
                {"policy_ref":"pii-redact-output@2","checkpoint":"DATA","target":"answer","action":"redact",\
                "matched":true,"score":0.9,"explanation":"entities: SA_MOBILE",\
                "spans":[{"start":9,"end":19,"entity":"SA_MOBILE","score":0.9}],\
                "output_text":"اتصل على [REDACTED:SA_MOBILE]","message_ar":null,"message_en":null,"error":null}"""));
        mvc.perform(post("/v1/admin/policies/test")
                        .header(HttpHeaders.AUTHORIZATION, bearer(adminToken()))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"policy_id\":\"pii-redact-output\",\"text\":\"اتصل على 0501234567\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.spans[0].entity").value("SA_MOBILE"))
                .andExpect(jsonPath("$.data.output_text").value("اتصل على [REDACTED:SA_MOBILE]"));
        AuditEvent event = singleAudit();
        assertThat(event.action()).isEqualTo("policy.test");
        assertThat(event.detail()).containsEntry("action", "redact").containsEntry("policy_ref", "pii-redact-output@2");
        assertThat(mapper.writeValueAsString(event.detail())).doesNotContain("0501234567");
    }

    @Test
    void versionsSwitchImportExportAndMetricsAreProxied() throws Exception {
        AGENT.enqueue(json(201, VIEW));
        mvc.perform(post("/v1/admin/policies/pii-redact-output/versions")
                        .header(HttpHeaders.AUTHORIZATION, bearer(adminToken()))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(POLICY))
                .andExpect(status().isCreated());
        assertThat(AGENT.takeRequest(5, TimeUnit.SECONDS).getPath()).isEqualTo("/v1/admin/policies/pii-redact-output/versions");

        AGENT.enqueue(json(200, VIEW.replace("\"enabled\":true", "\"enabled\":false")));
        mvc.perform(put("/v1/admin/policies/pii-redact-output/enabled")
                        .header(HttpHeaders.AUTHORIZATION, bearer(adminToken()))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"enabled\":false}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.policy.enabled").value(false));
        RecordedRequest switched = AGENT.takeRequest(5, TimeUnit.SECONDS);
        assertThat(switched.getMethod()).isEqualTo("PUT");

        AGENT.enqueue(json(200, "{\"yaml\":\"policies: []\\n\"}"));
        mvc.perform(get("/v1/admin/policies/export").header(HttpHeaders.AUTHORIZATION, bearer(adminToken())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.yaml").value("policies: []\n"));
        AGENT.takeRequest(5, TimeUnit.SECONDS);

        AGENT.enqueue(json(200, "{\"created\":[\"a\"],\"versioned\":[],\"switched\":[],\"unchanged\":[\"b\"]}"));
        mvc.perform(post("/v1/admin/policies/import")
                        .header(HttpHeaders.AUTHORIZATION, bearer(adminToken()))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"yaml\":\"policies: []\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.created[0]").value("a"));
        AGENT.takeRequest(5, TimeUnit.SECONDS);

        AGENT.enqueue(json(200, "{\"days\":7,\"rows\":[{\"day\":\"2026-10-06\",\"policy_id\":\"pii-redact-output\",\"action\":\"redact\",\"matches\":3}]}"));
        mvc.perform(get("/v1/admin/policies/metrics?days=7").header(HttpHeaders.AUTHORIZATION, bearer(adminToken())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.rows[0].matches").value(3));
        assertThat(AGENT.takeRequest(5, TimeUnit.SECONDS).getPath()).isEqualTo("/v1/admin/policies/metrics?days=7");

        assertThat(auditLog().events()).extracting(AuditEvent::action)
                .containsExactly("policy.version", "policy.enable", "policy.export", "policy.import", "policy.metrics");
    }

    @Test
    void anInvalidPolicyIdOrRangeIsRejectedWithoutCallingTheAgent() throws Exception {
        int before = AGENT.getRequestCount();
        mvc.perform(get("/v1/admin/policies/Not_An_Id").header(HttpHeaders.AUTHORIZATION, bearer(adminToken())))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error.code").value("invalid_request"));
        mvc.perform(get("/v1/admin/policies/metrics?days=500").header(HttpHeaders.AUTHORIZATION, bearer(adminToken())))
                .andExpect(status().isBadRequest());
        assertThat(AGENT.getRequestCount()).isEqualTo(before);
        assertThat(auditLog().events()).isEmpty();
    }

    @Test
    void anUnreachableAuditLogRefusesBeforeCallingTheAgent() throws Exception {
        auditLog().setMode(InMemoryAuditRepository.Mode.DOWN);
        int before = AGENT.getRequestCount();
        mvc.perform(get("/v1/admin/policies").header(HttpHeaders.AUTHORIZATION, bearer(adminToken())))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.error.code").value("audit_unavailable"));
        assertThat(AGENT.getRequestCount()).isEqualTo(before);
    }

    @Test
    void aFailedAuditWriteWithholdsTheAnswer() throws Exception {
        auditLog().setMode(InMemoryAuditRepository.Mode.WRITES_FAIL);
        AGENT.enqueue(json(200, "{\"policies\":[" + VIEW + "]}"));
        String body = mvc.perform(get("/v1/admin/policies").header(HttpHeaders.AUTHORIZATION, bearer(adminToken())))
                .andExpect(status().isServiceUnavailable())
                .andReturn().getResponse().getContentAsString(StandardCharsets.UTF_8);
        assertThat(body).doesNotContain("pii-redact-output");
    }

    @Test
    void anAgentFailureIsAgentUnavailable() throws Exception {
        AGENT.enqueue(json(500, "{}"));
        mvc.perform(get("/v1/admin/policies").header(HttpHeaders.AUTHORIZATION, bearer(adminToken())))
                .andExpect(status().isBadGateway())
                .andExpect(jsonPath("$.error.code").value("agent_unavailable"));
        assertThat(singleAudit().status()).isEqualTo(AuditStatus.ERROR);
    }

    @Test
    void savingAnExceptionIsAuditedWithWhatItOverrides() throws Exception {
        String saved = VIEW.replace("\"version\":2", "\"version\":1").replace("pii-redact-output", "pii-exempt-role")
                .replace("\"updated_by\":\"admin-1\"}", "\"updated_by\":\"admin-1\",\"overrides\":[\"pii-redact-output@1\"],\"exception\":true}");
        AGENT.enqueue(json(201, saved));
        mvc.perform(post("/v1/admin/policies")
                        .header(HttpHeaders.AUTHORIZATION, bearer(adminToken()))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(DRAFT.replace("keyword-test", "pii-exempt-role")))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.data.exception").value(true))
                .andExpect(jsonPath("$.data.overrides[0]").value("pii-redact-output@1"));
        AuditEvent event = singleAudit();
        assertThat(event.userId()).isEqualTo("user-123");
        assertThat(event.detail()).containsEntry("exception", true)
                .containsEntry("overrides", List.of("pii-redact-output@1"));
    }

    @Test
    void theOverridePreviewIsProxiedAndAudited() throws Exception {
        AGENT.enqueue(json(200, "{\"overrides\":[],\"exception\":false}"));
        mvc.perform(post("/v1/admin/policies/overrides")
                        .header(HttpHeaders.AUTHORIZATION, bearer(adminToken()))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(DRAFT))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.exception").value(false));
        assertThat(AGENT.takeRequest(5, TimeUnit.SECONDS).getPath()).isEqualTo("/v1/admin/policies/overrides");
        assertThat(singleAudit().action()).isEqualTo("policy.preview");
    }

    @Test
    void policyRolesAreReadFromKeycloakWithTheServiceAccount() throws Exception {
        KEYCLOAK_ADMIN.enqueue(json(200, "{\"access_token\":\"service-token\",\"expires_in\":60}"));
        KEYCLOAK_ADMIN.enqueue(json(200, "[{\"name\":\"policy:pii-exempt\"},{\"name\":\"policy:no-sql\"},{\"name\":\"admin\"}]"));
        mvc.perform(get("/v1/admin/policy-roles").header(HttpHeaders.AUTHORIZATION, bearer(adminToken())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.roles[0]").value("policy:no-sql"))
                .andExpect(jsonPath("$.data.roles[1]").value("policy:pii-exempt"))
                .andExpect(jsonPath("$.data.roles.length()").value(2));
        RecordedRequest tokenCall = KEYCLOAK_ADMIN.takeRequest(5, TimeUnit.SECONDS);
        assertThat(tokenCall.getPath()).isEqualTo("/realms/agent/protocol/openid-connect/token");
        assertThat(tokenCall.getBody().readUtf8()).contains("grant_type=client_credentials").contains("client_id=gateway");
        RecordedRequest rolesCall = KEYCLOAK_ADMIN.takeRequest(5, TimeUnit.SECONDS);
        assertThat(rolesCall.getPath()).startsWith("/admin/realms/agent/roles?search=policy%3A");
        assertThat(rolesCall.getHeader("Authorization")).isEqualTo("Bearer service-token");
        assertThat(singleAudit().action()).isEqualTo("policy.roles");
    }

    @Test
    void policyRolesArePagedAndFilteredByTheForwardingRule() throws Exception {
        StringBuilder page = new StringBuilder("[");
        for (int i = 0; i < KeycloakAdminClient.PAGE; i++) {
            page.append(i == 0 ? "" : ",").append(String.format("{\"name\":\"policy:r%03d\"}", i));
        }
        page.append("]");
        KEYCLOAK_ADMIN.enqueue(json(200, "{\"access_token\":\"service-token\",\"expires_in\":60}"));
        KEYCLOAK_ADMIN.enqueue(json(200, page.toString()));
        // Keycloak's search is a substring match: names the gateway would never forward are left out.
        KEYCLOAK_ADMIN.enqueue(json(200, "[{\"name\":\"policy:last\"},{\"name\":\"policy:Upper\"},{\"name\":\"xpolicy:a\"}]"));
        mvc.perform(get("/v1/admin/policy-roles").header(HttpHeaders.AUTHORIZATION, bearer(adminToken())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.roles.length()").value(KeycloakAdminClient.PAGE + 1))
                .andExpect(jsonPath("$.data.roles[0]").value("policy:last"));
        KEYCLOAK_ADMIN.takeRequest(5, TimeUnit.SECONDS);  // the token
        assertThat(KEYCLOAK_ADMIN.takeRequest(5, TimeUnit.SECONDS).getPath()).contains("first=0&max=100");
        assertThat(KEYCLOAK_ADMIN.takeRequest(5, TimeUnit.SECONDS).getPath()).contains("first=100&max=100");
    }

    @Test
    void theChangeLogIsProxiedAndAudited() throws Exception {
        AGENT.enqueue(json(200, "{\"changes\":[{\"changed_at\":\"2026-10-07T10:00:00Z\",\"policy_ref\":\"p-one@2\","
                + "\"change\":\"version\",\"actor\":\"user-123\",\"exception\":true,\"overrides\":[\"wide@1\"]}]}"));
        mvc.perform(get("/v1/admin/policies/p-one/changes").header(HttpHeaders.AUTHORIZATION, bearer(adminToken())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.changes[0].actor").value("user-123"))
                .andExpect(jsonPath("$.data.changes[0].exception").value(true));
        assertThat(AGENT.takeRequest(5, TimeUnit.SECONDS).getPath()).isEqualTo("/v1/admin/policies/p-one/changes");
        AuditEvent event = singleAudit();
        assertThat(event.action()).isEqualTo("policy.changes");
        assertThat(event.detail()).containsEntry("changes", 1);
    }

    @Test
    void policyRolesNeedAnAdminAndAReachableKeycloak() throws Exception {
        mvc.perform(get("/v1/admin/policy-roles").header(HttpHeaders.AUTHORIZATION, bearer(token())))
                .andExpect(status().isForbidden());
        assertThat(singleAudit().status()).isEqualTo(AuditStatus.DENIED);
        auditLog().clear();
        KEYCLOAK_ADMIN.enqueue(json(401, "{}"));
        mvc.perform(get("/v1/admin/policy-roles").header(HttpHeaders.AUTHORIZATION, bearer(adminToken())))
                .andExpect(status().isBadGateway())
                .andExpect(jsonPath("$.error.code").value("identity_unavailable"));
        assertThat(singleAudit().status()).isEqualTo(AuditStatus.ERROR);
    }

    private static MockResponse json(int status, String body) {
        return new MockResponse().setResponseCode(status).setHeader("Content-Type", "application/json").setBody(body);
    }

    private AuditEvent singleAudit() {
        List<AuditEvent> events = auditLog().events();
        assertThat(events).hasSize(1);
        return events.getFirst();
    }
}
