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

    private static MockResponse json(int status, String body) {
        return new MockResponse().setResponseCode(status).setHeader("Content-Type", "application/json").setBody(body);
    }

    private AuditEvent singleAudit() {
        List<AuditEvent> events = auditLog().events();
        assertThat(events).hasSize(1);
        return events.getFirst();
    }
}
