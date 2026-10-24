package sa.ncai.agent.gateway.skills;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;
import sa.ncai.agent.gateway.GatewayTestSupport;
import sa.ncai.agent.gateway.audit.AuditEvent;
import sa.ncai.agent.gateway.audit.AuditStatus;
import sa.ncai.agent.gateway.conversations.ConversationMessages;

/**
 * Skills at the gateway (phase 06): permission first (G6). A picked skill needs the caller's
 * {@code skill:{slug}} role, checked here before the agent is called; the {@code skill:*} roles are
 * forwarded as permissions; the applied skill reaches the audit record as slug@version (G8); the
 * picker's list is the agent's, filtered by the permissions sent from the verified token. The slug
 * here is a test name: no gateway file names a skill (G5).
 */
class SkillProxyTest extends GatewayTestSupport {

    private static final String CONVERSATION_ID = "0b4c9a52-4a7e-4a8e-9f55-1d6d0c7b2e66";
    private static final String SKILL = "fixture_form";
    private static final String DONE = """
            event: skill
            id: 1
            data: {"type":"skill","request_id":"r","turn_id":"t","seq":1,"data":{"slug":"fixture_form","label":{"ar":"نموذج","en":"Form"}}}

            event: done
            id: 2
            data: {"type":"done","request_id":"r","turn_id":"t","seq":2,"data":{"answer":"نص","citations":[],"usage":{"tokens_in":1,"tokens_out":1,"cost_usd":0.0},"guard_events":[],"state_digest":{"turn":1,"capabilities":["general"],"skill":"fixture_form@1.0.0"},"trace_id":"4bf92f3577b34da6a3ce929d0e0e4736"}}

            """;

    @Autowired
    ObjectMapper mapper;

    private static String withRoles(String... roles) {
        return token(c -> c.claim("realm_access", Map.of("roles", List.of(roles))));
    }

    private MockHttpServletRequestBuilder chat(String token, String skill) throws Exception {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("conversation_id", CONVERSATION_ID);
        body.put("message", "اكتب نموذجًا");
        if (skill != null) {
            body.put("skill", skill);
        }
        return post("/v1/chat/turns")
                .header(HttpHeaders.AUTHORIZATION, bearer(token))
                .contentType(MediaType.APPLICATION_JSON)
                .accept(MediaType.TEXT_EVENT_STREAM)
                .characterEncoding(StandardCharsets.UTF_8)
                .content(mapper.writeValueAsString(body));
    }

    @Test
    void aPickedSkillTheCallerLacksIsRefusedBeforeTheAgentAndAudited() throws Exception {
        int before = AGENT.getRequestCount();
        mvc.perform(chat(withRoles("user", "cap:general"), SKILL))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error.code").value("skill_forbidden"))
                .andExpect(jsonPath("$.error.message_ar").isNotEmpty());
        assertThat(AGENT.getRequestCount()).isEqualTo(before);
        AuditEvent event = auditLog().events().getLast();
        assertThat(event.status()).isEqualTo(AuditStatus.DENIED);
        assertThat(event.detail()).containsEntry("error_code", "skill_forbidden");
        assertThat(event.detail()).containsEntry("skill_requested", SKILL);
    }

    @Test
    void aLookalikeRoleGrantsNoSkill() throws Exception {
        mvc.perform(chat(withRoles("user", "cap:general", "cap:" + SKILL, "kb:" + SKILL + ":read"), SKILL))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error.code").value("skill_forbidden"));
    }

    @Test
    void anInvalidSkillSlugIs400() throws Exception {
        mvc.perform(chat(withRoles("user", "cap:general", "skill:x"), "skill:x"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error.code").value("invalid_request"));
    }

    @Test
    void aPickedSkillTheCallerHoldsIsForwardedAndTheAppliedSkillAudited() throws Exception {
        AGENT.enqueue(new MockResponse().setHeader("Content-Type", "text/event-stream; charset=utf-8").setBody(DONE));
        mvc.perform(chat(withRoles("user", "cap:general", "skill:" + SKILL), SKILL)).andExpect(status().isOk());

        RecordedRequest upstream = AGENT.takeRequest(5, TimeUnit.SECONDS);
        JsonNode sent = mapper.readTree(upstream.getBody().readUtf8());
        assertThat(sent.path("skill").asText()).isEqualTo(SKILL);
        assertThat(sent.path("principal").path("permissions").toString())
                .isEqualTo("[\"cap:general\",\"skill:" + SKILL + "\"]");
        AuditEvent event = auditLog().events().getLast();
        assertThat(event.status()).isEqualTo(AuditStatus.OK);
        assertThat(event.detail()).containsEntry("skill", SKILL + "@1.0.0");
        assertThat(event.detail()).containsEntry("skill_requested", SKILL);
    }

    @Test
    void withoutAPickedSkillTheRequestNamesNone() throws Exception {
        AGENT.enqueue(new MockResponse().setHeader("Content-Type", "text/event-stream; charset=utf-8").setBody(DONE));
        mvc.perform(chat(withRoles("user", "cap:general", "skill:" + SKILL), null)).andExpect(status().isOk());
        JsonNode sent = mapper.readTree(AGENT.takeRequest(5, TimeUnit.SECONDS).getBody().readUtf8());
        assertThat(sent.has("skill")).isFalse();
        assertThat(auditLog().events().getLast().detail()).doesNotContainKey("skill_requested");
    }

    @Test
    void theAgentsSkillRefusalIs403SkillForbidden() throws Exception {
        AGENT.enqueue(new MockResponse()
                .setResponseCode(403)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"request_id\":\"r\",\"error\":{\"code\":\"skill_forbidden\",\"message_ar\":\"x\",\"message_en\":\"x\"}}"));
        mvc.perform(chat(withRoles("user", "cap:general", "skill:" + SKILL), SKILL))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error.code").value("skill_forbidden"));
        AuditEvent event = auditLog().events().getLast();
        assertThat(event.status()).isEqualTo(AuditStatus.DENIED);
        assertThat(event.detail()).containsEntry("error_code", "skill_forbidden");
    }

    @Test
    void theSkillsListIsTheAgentsFilteredByThePermissionsOfTheToken() throws Exception {
        AGENT.enqueue(new MockResponse().setHeader("Content-Type", "application/json").setBody(
                "{\"skills\":[{\"slug\":\"fixture_form\",\"name\":{\"ar\":\"نموذج\",\"en\":\"Form\"},"
                        + "\"description\":{\"ar\":\"وصف\",\"en\":\"Description\"}}]}"));
        mvc.perform(get("/v1/skills").header(HttpHeaders.AUTHORIZATION,
                        bearer(withRoles("user", "cap:general", "skill:" + SKILL, "admin", "policy:pii-exempt"))))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.skills[0].slug").value(SKILL))
                .andExpect(jsonPath("$.data.skills[0].name.ar").value("نموذج"))
                .andExpect(jsonPath("$.request_id").isNotEmpty());
        RecordedRequest upstream = AGENT.takeRequest(5, TimeUnit.SECONDS);
        assertThat(upstream.getPath()).isEqualTo("/v1/skills");
        assertThat(upstream.getHeader("X-Internal-Token")).isEqualTo(INTERNAL_TOKEN);
        assertThat(upstream.getHeader("X-User-Id")).isEqualTo("user-123");
        // Only permissions leave the gateway: no admin or policy role (ADR-0046, ADR-0107).
        assertThat(upstream.getHeader("X-User-Permissions")).isEqualTo("cap:general,skill:" + SKILL);
    }

    @Test
    void theSkillsListNeedsAuthenticationAndAnAgentFailureIs502() throws Exception {
        mvc.perform(get("/v1/skills")).andExpect(status().isUnauthorized());
        AGENT.enqueue(new MockResponse().setResponseCode(500));
        mvc.perform(get("/v1/skills").header(HttpHeaders.AUTHORIZATION, bearer(token())))
                .andExpect(status().isBadGateway())
                .andExpect(jsonPath("$.error.code").value("agent_unavailable"));
    }

    @Test
    void aHistoryReplyKeepsItsSkill() throws Exception {
        String agentJson = "{\"conversation_id\":\"c\",\"messages\":[{\"role\":\"assistant\",\"text\":\"نص\","
                + "\"turn_id\":\"t\",\"attachments\":[],\"citations\":[],"
                + "\"skill\":{\"slug\":\"fixture_form\",\"label\":{\"ar\":\"نموذج\",\"en\":\"Form\"}}},"
                + "{\"role\":\"user\",\"text\":\"س\",\"attachments\":[],\"citations\":[]}]}";
        ConversationMessages read = mapper.readValue(agentJson, ConversationMessages.class);
        JsonNode written = mapper.readTree(mapper.writeValueAsString(read));
        assertThat(written.path("messages").get(0).path("skill").path("slug").asText()).isEqualTo(SKILL);
        assertThat(written.path("messages").get(0).path("skill").path("label").path("ar").asText()).isEqualTo("نموذج");
        JsonNode none = written.path("messages").get(1).path("skill");
        assertThat(none.isMissingNode() || none.isNull()).isTrue();
    }
}
