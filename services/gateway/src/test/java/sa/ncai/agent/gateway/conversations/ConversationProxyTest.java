package sa.ncai.agent.gateway.conversations;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.concurrent.TimeUnit;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpHeaders;
import sa.ncai.agent.gateway.GatewayTestSupport;
import sa.ncai.agent.gateway.InMemoryAuditRepository;
import sa.ncai.agent.gateway.audit.AuditEvent;
import sa.ncai.agent.gateway.audit.AuditStatus;

/**
 * The conversation list and history (ADR-0192 to ADR-0194): the caller comes from the token only,
 * another owner's conversation is refused before the agent is called, and both reads are audited
 * fail-closed with counts, never message text.
 */
class ConversationProxyTest extends GatewayTestSupport {

    private static final String CONVERSATION = "6a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d";

    private static final String LIST = """
            {"conversations":[{"id":"6a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d","title":"ما عاصمة المملكة؟",\
            "last_turn_at":"2026-10-15T07:10:14.986705Z"}],"next_before":"2026-10-15T07:10:14.986705Z"}""";

    private static final String MESSAGES = """
            {"conversation_id":"6a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d","messages":[\
            {"role":"user","text":"لخص الملف","turn_id":null,\
            "attachments":[{"id":"a1","name":"لائحة.pdf"}],"citations":[]},\
            {"role":"assistant","text":"الملخص","turn_id":"0123456789abcdef0123456789abcdef","attachments":[],\
            "citations":[{"id":"a1","title":"لائحة.pdf","source":"doc","url":null,"kb":null,"locator":null,\
            "document_id":"a1","pages":[2,3]}]}]}""";

    @Test
    void listForwardsTheCallerFromTheTokenAndThePageAndAuditsTheCount() throws Exception {
        AGENT.enqueue(json(200, LIST));

        mvc.perform(get("/v1/conversations").param("limit", "10").param("before", "2026-10-15T08:00:00Z")
                        .header(HttpHeaders.AUTHORIZATION, bearer(token()))
                        // A client cannot name another caller: these never reach the agent.
                        .header("X-User-Id", "victim")
                        .header("X-Tenant-Id", "tenant-z"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.conversations[0].id").value(CONVERSATION))
                .andExpect(jsonPath("$.data.conversations[0].title").value("ما عاصمة المملكة؟"))
                .andExpect(jsonPath("$.data.conversations[0].last_turn_at").value("2026-10-15T07:10:14.986705Z"))
                .andExpect(jsonPath("$.data.next_before").value("2026-10-15T07:10:14.986705Z"));

        RecordedRequest upstream = AGENT.takeRequest(5, TimeUnit.SECONDS);
        assertThat(upstream.getPath()).isEqualTo("/v1/conversations?limit=10&before=2026-10-15T08:00:00Z");
        assertThat(upstream.getHeader("X-Internal-Token")).isEqualTo(INTERNAL_TOKEN);
        assertThat(upstream.getHeaders().values("X-User-Id")).containsExactly("user-123");
        assertThat(upstream.getHeaders().values("X-Tenant-Id")).containsExactly("tenant-a");

        AuditEvent event = singleAudit();
        assertThat(event.action()).isEqualTo("conversation.list");
        assertThat(event.status()).isEqualTo(AuditStatus.OK);
        assertThat(event.detail()).containsEntry("conversations", 1).containsKey("latency_ms");
        assertThat(event.detail().toString()).doesNotContain("عاصمة");
    }

    @Test
    void listDefaultsToThirtyAndRejectsABadPageWithoutCallingTheAgent() throws Exception {
        AGENT.enqueue(json(200, "{\"conversations\":[]}"));
        mvc.perform(get("/v1/conversations").header(HttpHeaders.AUTHORIZATION, bearer(token())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.conversations").isEmpty());
        assertThat(AGENT.takeRequest(5, TimeUnit.SECONDS).getPath()).isEqualTo("/v1/conversations?limit=30");

        for (String[] param : List.of(new String[] {"limit", "0"}, new String[] {"limit", "101"},
                new String[] {"limit", "x"}, new String[] {"before", "yesterday"})) {
            mvc.perform(get("/v1/conversations").param(param[0], param[1])
                            .header(HttpHeaders.AUTHORIZATION, bearer(token())))
                    .andExpect(status().isBadRequest())
                    .andExpect(jsonPath("$.error.code").value("invalid_request"));
        }
        assertThat(AGENT.takeRequest(200, TimeUnit.MILLISECONDS)).isNull();
    }

    @Test
    void messagesForwardTheLowerCaseIdForTheOwnerAndAuditTheCount() throws Exception {
        conversations.bindOrGetOwner(CONVERSATION, "user-123", "tenant-a");
        AGENT.enqueue(json(200, MESSAGES));

        mvc.perform(get("/v1/conversations/" + CONVERSATION.toUpperCase() + "/messages")
                        .header(HttpHeaders.AUTHORIZATION, bearer(token())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.conversation_id").value(CONVERSATION))
                .andExpect(jsonPath("$.data.messages[0].attachments[0].name").value("لائحة.pdf"))
                .andExpect(jsonPath("$.data.messages[1].turn_id").value("0123456789abcdef0123456789abcdef"))
                .andExpect(jsonPath("$.data.messages[1].citations[0].pages[1]").value(3))
                .andExpect(jsonPath("$.data.messages[1].citations[0].document_id").value("a1"));

        RecordedRequest upstream = AGENT.takeRequest(5, TimeUnit.SECONDS);
        assertThat(upstream.getPath()).isEqualTo("/v1/conversations/" + CONVERSATION + "/messages");
        assertThat(upstream.getHeaders().values("X-User-Id")).containsExactly("user-123");

        AuditEvent event = singleAudit();
        assertThat(event.action()).isEqualTo("conversation.read");
        assertThat(event.conversationId()).isEqualTo(CONVERSATION);
        assertThat(event.detail()).containsEntry("messages", 2);
        assertThat(event.detail().toString()).doesNotContain("الملخص");
    }

    @Test
    void anotherUsersOrTenantsConversationIsForbiddenAndNeverReachesTheAgent() throws Exception {
        for (String[] owner : List.of(new String[] {"viewer-1", "tenant-a"}, new String[] {"user-123", "tenant-b"})) {
            ((sa.ncai.agent.gateway.InMemoryConversationRepository) conversations).clear();
            auditLog().clear();
            conversations.bindOrGetOwner(CONVERSATION, owner[0], owner[1]);

            mvc.perform(get("/v1/conversations/" + CONVERSATION + "/messages")
                            .header(HttpHeaders.AUTHORIZATION, bearer(token())))
                    .andExpect(status().isForbidden())
                    .andExpect(jsonPath("$.error.code").value("conversation_forbidden"));

            AuditEvent event = singleAudit();
            assertThat(event.status()).isEqualTo(AuditStatus.DENIED);
            assertThat(event.detail()).containsEntry("error_code", "conversation_forbidden");
        }
        assertThat(AGENT.takeRequest(200, TimeUnit.MILLISECONDS)).isNull();
    }

    @Test
    void theAgentsOwnRefusalIsForwardedAsForbidden() throws Exception {
        conversations.bindOrGetOwner(CONVERSATION, "user-123", "tenant-a");
        AGENT.enqueue(json(403, "{\"request_id\":\"x\",\"error\":{\"code\":\"conversation_forbidden\","
                + "\"message_ar\":\"م\",\"message_en\":\"m\",\"retryable\":false}}"));

        mvc.perform(get("/v1/conversations/" + CONVERSATION + "/messages")
                        .header(HttpHeaders.AUTHORIZATION, bearer(token())))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error.code").value("conversation_forbidden"));
        assertThat(singleAudit().status()).isEqualTo(AuditStatus.DENIED);
    }

    @Test
    void aConversationNobodyUsedIsEmptyAndBindsNobody() throws Exception {
        mvc.perform(get("/v1/conversations/" + CONVERSATION + "/messages")
                        .header(HttpHeaders.AUTHORIZATION, bearer(token())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.messages").isEmpty());
        assertThat(conversations.ownerOf(CONVERSATION)).isEmpty();
        assertThat(AGENT.takeRequest(200, TimeUnit.MILLISECONDS)).isNull();
    }

    @Test
    void aMalformedIdIsRejectedWithoutAuditOrAgent() throws Exception {
        mvc.perform(get("/v1/conversations/not-a-uuid/messages").header(HttpHeaders.AUTHORIZATION, bearer(token())))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error.code").value("invalid_request"));
        assertThat(auditLog().events()).isEmpty();
        assertThat(AGENT.takeRequest(200, TimeUnit.MILLISECONDS)).isNull();
    }

    @Test
    void anUnreachableAuditLogRefusesBeforeCallingTheAgent() throws Exception {
        conversations.bindOrGetOwner(CONVERSATION, "user-123", "tenant-a");
        auditLog().setMode(InMemoryAuditRepository.Mode.DOWN);
        for (String path : List.of("/v1/conversations", "/v1/conversations/" + CONVERSATION + "/messages")) {
            mvc.perform(get(path).header(HttpHeaders.AUTHORIZATION, bearer(token())))
                    .andExpect(status().isServiceUnavailable())
                    .andExpect(jsonPath("$.error.code").value("audit_unavailable"));
        }
        assertThat(AGENT.takeRequest(200, TimeUnit.MILLISECONDS)).isNull();
    }

    @Test
    void aFailedAuditWriteWithholdsTheHistory() throws Exception {
        conversations.bindOrGetOwner(CONVERSATION, "user-123", "tenant-a");
        auditLog().setMode(InMemoryAuditRepository.Mode.WRITES_FAIL);
        AGENT.enqueue(json(200, MESSAGES));

        String body = mvc.perform(get("/v1/conversations/" + CONVERSATION + "/messages")
                        .header(HttpHeaders.AUTHORIZATION, bearer(token())))
                .andExpect(status().isServiceUnavailable())
                .andReturn().getResponse().getContentAsString(StandardCharsets.UTF_8);
        assertThat(body).doesNotContain("الملخص");
    }

    @Test
    void anAgentFailureIsAgentUnavailableAndAudited() throws Exception {
        AGENT.enqueue(json(500, "{}"));
        mvc.perform(get("/v1/conversations").header(HttpHeaders.AUTHORIZATION, bearer(token())))
                .andExpect(status().isBadGateway())
                .andExpect(jsonPath("$.error.code").value("agent_unavailable"));
        assertThat(singleAudit().status()).isEqualTo(AuditStatus.ERROR);
    }

    @Test
    void bothRequireAuthentication() throws Exception {
        for (String path : List.of("/v1/conversations", "/v1/conversations/" + CONVERSATION + "/messages")) {
            mvc.perform(get(path)).andExpect(status().isUnauthorized());
        }
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
