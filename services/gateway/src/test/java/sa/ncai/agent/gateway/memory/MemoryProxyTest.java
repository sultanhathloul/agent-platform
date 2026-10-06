package sa.ncai.agent.gateway.memory;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.util.List;
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

/** The memory endpoints proxy to the agent with the caller from the token, audited fail-closed (ADR-0042, ADR-0059). */
class MemoryProxyTest extends GatewayTestSupport {

    private static final String ITEM_ID = "3f2c1b7e-9a4d-4c1e-8b2f-5d6e7a8b9c0d";

    private static final String MEMORY = """
            {"enabled":true,"items":[{"id":"3f2c1b7e-9a4d-4c1e-8b2f-5d6e7a8b9c0d","kind":"preference",\
            "text":"يفضّل المستخدم الإجابات المختصرة.","confidence":0.9,"created_at":"2026-09-15T10:00:00Z",\
            "expires_at":null}]}""";

    @Autowired
    ObjectMapper mapper;

    @Test
    void listForwardsTheCallerFromTheTokenAndAuditsTheRequest() throws Exception {
        AGENT.enqueue(json(200, MEMORY));

        mvc.perform(get("/v1/memory")
                        .header(HttpHeaders.AUTHORIZATION, bearer(token()))
                        // A client cannot name another caller: these never reach the agent.
                        .header(AgentHeaders.USER, "victim")
                        .header(AgentHeaders.TENANT, "tenant-z"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.request_id").isNotEmpty())
                .andExpect(jsonPath("$.data.enabled").value(true))
                .andExpect(jsonPath("$.data.items[0].id").value(ITEM_ID))
                .andExpect(jsonPath("$.data.items[0].text").value("يفضّل المستخدم الإجابات المختصرة."))
                .andExpect(jsonPath("$.data.items[0].created_at").value("2026-09-15T10:00:00Z"));

        RecordedRequest upstream = AGENT.takeRequest(5, TimeUnit.SECONDS);
        assertThat(upstream).isNotNull();
        assertThat(upstream.getMethod()).isEqualTo("GET");
        assertThat(upstream.getPath()).isEqualTo("/v1/memory");
        assertThat(upstream.getHeader("X-Internal-Token")).isEqualTo(INTERNAL_TOKEN);
        assertThat(upstream.getHeaders().values(AgentHeaders.USER)).containsExactly("user-123");
        assertThat(upstream.getHeaders().values(AgentHeaders.TENANT)).containsExactly("tenant-a");

        AuditEvent event = singleAudit();
        assertThat(event.action()).isEqualTo("memory.list");
        assertThat(event.status()).isEqualTo(AuditStatus.OK);
        assertThat(event.userId()).isEqualTo("user-123");
        assertThat(event.tenantId()).isEqualTo("tenant-a");
        assertThat(event.conversationId()).isNull();
        assertThat(event.detail()).containsEntry("items", 1).containsEntry("enabled", true).containsKey("latency_ms");
    }

    @Test
    void deleteForwardsTheLowerCaseIdAndAuditsIt() throws Exception {
        AGENT.enqueue(new MockResponse().setResponseCode(204));

        mvc.perform(delete("/v1/memory/" + ITEM_ID.toUpperCase()).header(HttpHeaders.AUTHORIZATION, bearer(token())))
                .andExpect(status().isNoContent());

        RecordedRequest upstream = AGENT.takeRequest(5, TimeUnit.SECONDS);
        assertThat(upstream.getMethod()).isEqualTo("DELETE");
        assertThat(upstream.getPath()).isEqualTo("/v1/memory/" + ITEM_ID);
        assertThat(upstream.getHeader(AgentHeaders.USER)).isEqualTo("user-123");
        AuditEvent event = singleAudit();
        assertThat(event.action()).isEqualTo("memory.delete");
        assertThat(event.status()).isEqualTo(AuditStatus.OK);
        assertThat(event.detail()).containsEntry("item_id", ITEM_ID);
    }

    @Test
    void anItemTheCallerDoesNotHaveIsMemoryNotFound() throws Exception {
        AGENT.enqueue(json(404, """
                {"request_id":"r","error":{"code":"memory_not_found","message_ar":"غير موجود","message_en":"not found"}}"""));

        mvc.perform(delete("/v1/memory/" + ITEM_ID).header(HttpHeaders.AUTHORIZATION, bearer(token())))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error.code").value("memory_not_found"))
                .andExpect(jsonPath("$.error.message_ar").isNotEmpty());

        AuditEvent event = singleAudit();
        assertThat(event.status()).isEqualTo(AuditStatus.DENIED);
        assertThat(event.detail()).containsEntry("error_code", "memory_not_found");
    }

    @Test
    void anInvalidItemIdIsRejectedWithoutCallingTheAgent() throws Exception {
        int before = AGENT.getRequestCount();

        mvc.perform(delete("/v1/memory/not-an-id").header(HttpHeaders.AUTHORIZATION, bearer(token())))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error.code").value("invalid_request"));

        assertThat(AGENT.getRequestCount()).isEqualTo(before);
        assertThat(auditLog().events()).isEmpty();
    }

    @Test
    void settingsForwardTheSwitch() throws Exception {
        AGENT.enqueue(json(200, "{\"enabled\":false}"));

        mvc.perform(put("/v1/memory/settings")
                        .header(HttpHeaders.AUTHORIZATION, bearer(token()))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"enabled\":false}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.enabled").value(false));

        RecordedRequest upstream = AGENT.takeRequest(5, TimeUnit.SECONDS);
        assertThat(upstream.getMethod()).isEqualTo("PUT");
        assertThat(upstream.getPath()).isEqualTo("/v1/memory/settings");
        JsonNode sent = mapper.readTree(upstream.getBody().readUtf8());
        assertThat(sent.toString()).isEqualTo("{\"enabled\":false}");
        assertThat(upstream.getHeader(AgentHeaders.TENANT)).isEqualTo("tenant-a");
        AuditEvent event = singleAudit();
        assertThat(event.action()).isEqualTo("memory.settings");
        assertThat(event.detail()).containsEntry("enabled", false);
    }

    @Test
    void settingsWithoutTheSwitchAreInvalid() throws Exception {
        int before = AGENT.getRequestCount();
        mvc.perform(put("/v1/memory/settings")
                        .header(HttpHeaders.AUTHORIZATION, bearer(token()))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error.code").value("invalid_request"));
        assertThat(AGENT.getRequestCount()).isEqualTo(before);
    }

    @Test
    void anUnreachableAuditLogRefusesBeforeCallingTheAgent() throws Exception {
        auditLog().setMode(InMemoryAuditRepository.Mode.DOWN);
        int before = AGENT.getRequestCount();

        for (var request : List.of(
                get("/v1/memory"),
                delete("/v1/memory/" + ITEM_ID),
                put("/v1/memory/settings").contentType(MediaType.APPLICATION_JSON).content("{\"enabled\":true}"))) {
            mvc.perform(request.header(HttpHeaders.AUTHORIZATION, bearer(token())))
                    .andExpect(status().isServiceUnavailable())
                    .andExpect(jsonPath("$.error.code").value("audit_unavailable"));
        }
        assertThat(AGENT.getRequestCount()).isEqualTo(before);
    }

    @Test
    void aFailedAuditWriteWithholdsTheMemory() throws Exception {
        auditLog().setMode(InMemoryAuditRepository.Mode.WRITES_FAIL);
        AGENT.enqueue(json(200, MEMORY));

        String body = mvc.perform(get("/v1/memory").header(HttpHeaders.AUTHORIZATION, bearer(token())))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.error.code").value("audit_unavailable"))
                .andReturn().getResponse().getContentAsString(StandardCharsets.UTF_8);

        assertThat(body).doesNotContain("الإجابات المختصرة");
    }

    @Test
    void anAgentFailureIsAgentUnavailableAndAudited() throws Exception {
        AGENT.enqueue(json(500, "{}"));

        mvc.perform(get("/v1/memory").header(HttpHeaders.AUTHORIZATION, bearer(token())))
                .andExpect(status().isBadGateway())
                .andExpect(jsonPath("$.error.code").value("agent_unavailable"));

        AuditEvent event = singleAudit();
        assertThat(event.status()).isEqualTo(AuditStatus.ERROR);
        assertThat(event.detail()).containsEntry("error_code", "agent_unavailable");
    }

    @Test
    void memoryRequiresAuthentication() throws Exception {
        mvc.perform(get("/v1/memory"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.error.code").value("unauthorized"));
    }

    private static MockResponse json(int status, String body) {
        return new MockResponse()
                .setResponseCode(status)
                .setHeader("Content-Type", "application/json")
                .setBody(body);
    }

    private AuditEvent singleAudit() {
        List<AuditEvent> events = auditLog().events();
        assertThat(events).hasSize(1);
        return events.getFirst();
    }

    /** The agent's caller headers, spelled out so the test does not depend on the client's constants. */
    private static final class AgentHeaders {
        static final String USER = "X-User-Id";
        static final String TENANT = "X-Tenant-Id";
    }
}
