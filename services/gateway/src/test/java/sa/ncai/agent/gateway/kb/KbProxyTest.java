package sa.ncai.agent.gateway.kb;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.multipart;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;
import org.springframework.test.web.servlet.request.MockMultipartHttpServletRequestBuilder;
import sa.ncai.agent.gateway.GatewayTestSupport;
import sa.ncai.agent.gateway.InMemoryObjectStorage;
import sa.ncai.agent.gateway.audit.AuditEvent;
import sa.ncai.agent.gateway.audit.AuditStatus;

/**
 * Knowledge bases at the gateway (phase 08, ADR-0333 to ADR-0339). Negative permission first (I7):
 * a non-admin is refused on every admin endpoint before Keycloak or the agent hears of it, audited;
 * a turn selecting a KB the token does not hold, or one the agent's records say is not live for the
 * caller's tenant (deleted, another tenant's, unknown), is refused 403 kb_forbidden before the turn.
 * Then: creating a KB creates its realm role first, bounded; uploads carry the D3 metadata; the
 * composer's list is the agent's, filtered by the token's permissions.
 */
class KbProxyTest extends GatewayTestSupport {

    private static final String SLUG = "intl-economy";
    private static final String CONVERSATION_ID = "0b4c9a52-4a7e-4a8e-9f55-1d6d0c7b2e66";
    private static final String KB_VIEW = """
            {"slug":"intl-economy","name":{"ar":"الاقتصاد الدولي","en":"International economy"},\
            "description":{"ar":"","en":""},"icon":"library","role":"kb:intl-economy:read","documents":0,\
            "indexed":0,"created_at":"2026-10-26T12:00:00Z","created_by_username":"admin","created_by_name":"مدير"}""";
    private static final String DOCUMENT_VIEW = """
            {"id":"6f1c0a2e-3b4d-4e5f-8a9b-0c1d2e3f4a5b","kb":"intl-economy","name":"weo.pdf","kind":"pdf","size":42,\
            "status":"pending","metadata":{"title":"آفاق الاقتصاد العالمي","publisher":"صندوق النقد الدولي",\
            "source_url":"https://example.org/weo","published_on":"2026-07-15","supersedes":null},\
            "created_at":"2026-10-26T12:00:00Z"}""";

    @Autowired
    ObjectMapper mapper;

    private static String admin() {
        return token(c -> c.claim("preferred_username", "admin").claim("name", "مدير")
                .claim("realm_access", Map.of("roles", List.of("user", "admin", "cap:general"))));
    }

    private static String withRoles(String... roles) {
        return token(c -> c.claim("realm_access", Map.of("roles", List.of(roles))));
    }

    private static MockResponse json(int status, String body) {
        return new MockResponse().setResponseCode(status).setHeader("Content-Type", "application/json").setBody(body);
    }

    private static byte[] pdf() {
        return "%PDF-1.7\n%âãÏÓ\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF".getBytes(StandardCharsets.ISO_8859_1);
    }

    private MockMultipartHttpServletRequestBuilder upload(String token, String fileName, String publishedOn) {
        MockMultipartHttpServletRequestBuilder builder = multipart("/v1/admin/kbs/" + SLUG + "/documents");
        builder.file(new MockMultipartFile("file", fileName, "application/octet-stream", pdf()));
        builder.param("title", "آفاق الاقتصاد العالمي").param("publisher", "صندوق النقد الدولي")
                .param("published_on", publishedOn).param("source_url", "https://example.org/weo");
        builder.header(HttpHeaders.AUTHORIZATION, bearer(token));
        return builder;
    }

    private MockHttpServletRequestBuilder createRequest(String token) throws Exception {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("slug", SLUG);
        body.put("name", Map.of("ar", "الاقتصاد الدولي", "en", "International economy"));
        return post("/v1/admin/kbs").header(HttpHeaders.AUTHORIZATION, bearer(token))
                .contentType(MediaType.APPLICATION_JSON).characterEncoding(StandardCharsets.UTF_8)
                .content(mapper.writeValueAsString(body));
    }

    private InMemoryObjectStorage objects() {
        return (InMemoryObjectStorage) storage;
    }

    // ------------------------------------------------------------------ negative permission first (I7)

    @Test
    void aNonAdminIsRefusedOnEveryKbEndpointBeforeKeycloakOrTheAgentAndAudited() throws Exception {
        String analyst = withRoles("user", "cap:general", "cap:kb", "kb:" + SLUG + ":read");
        String doc = "/v1/admin/kbs/" + SLUG + "/documents/6f1c0a2e-3b4d-4e5f-8a9b-0c1d2e3f4a5b";
        List<MockHttpServletRequestBuilder> calls = List.of(
                get("/v1/admin/kbs"),
                get("/v1/admin/kbs/" + SLUG),
                delete("/v1/admin/kbs/" + SLUG),
                get("/v1/admin/kbs/" + SLUG + "/documents"),
                get(doc),
                put(doc + "/metadata").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"title\":\"t\",\"publisher\":\"p\",\"published_on\":\"2026-01-01\"}"));
        int agentBefore = AGENT.getRequestCount();
        int keycloakBefore = KEYCLOAK_ADMIN.getRequestCount();
        for (MockHttpServletRequestBuilder call : calls) {
            mvc.perform(call.header(HttpHeaders.AUTHORIZATION, bearer(analyst)))
                    .andExpect(status().isForbidden()).andExpect(jsonPath("$.error.code").value("forbidden"));
        }
        mvc.perform(createRequest(analyst)).andExpect(status().isForbidden());
        mvc.perform(upload(analyst, "weo.pdf", "2026-07-15"))
                .andExpect(status().isForbidden()).andExpect(jsonPath("$.error.code").value("forbidden"));
        assertThat(AGENT.getRequestCount()).isEqualTo(agentBefore);
        assertThat(KEYCLOAK_ADMIN.getRequestCount()).isEqualTo(keycloakBefore);
        assertThat(objects().objects).isEmpty();
        List<AuditEvent> events = auditLog().events();
        assertThat(events).hasSize(calls.size() + 2);
        assertThat(events).allSatisfy(event -> {
            assertThat(event.status()).isEqualTo(AuditStatus.DENIED);
            assertThat(event.detail()).containsEntry("error_code", "forbidden");
        });
    }

    @Test
    void aTurnSelectingAKbTheTokenDoesNotHoldIsRefusedBeforeTheAgent() throws Exception {
        int before = AGENT.getRequestCount();
        mvc.perform(chat(withRoles("user", "cap:general", "cap:kb"), List.of(SLUG)))
                .andExpect(status().isForbidden()).andExpect(jsonPath("$.error.code").value("kb_forbidden"));
        assertThat(AGENT.getRequestCount()).isEqualTo(before);
        AuditEvent event = auditLog().events().getLast();
        assertThat(event.status()).isEqualTo(AuditStatus.DENIED);
        assertThat(event.detail()).containsEntry("denied_kbs", List.of(SLUG));
    }

    @Test
    void aTurnSelectingAHeldKbThatIsNotLiveIsRefusedWithoutTheTurn() throws Exception {
        // Deleted, another tenant's or unknown: the agent's records do not list it as live.
        AGENT.enqueue(json(200, "{\"live\":[]}"));
        mvc.perform(chat(withRoles("user", "cap:general", "cap:kb", "kb:" + SLUG + ":read"), List.of(SLUG)))
                .andExpect(status().isForbidden()).andExpect(jsonPath("$.error.code").value("kb_forbidden"));
        RecordedRequest check = AGENT.takeRequest(5, TimeUnit.SECONDS);
        assertThat(check.getPath()).isEqualTo("/v1/internal/kbs/live?slug=" + SLUG);
        assertThat(AGENT.takeRequest(200, TimeUnit.MILLISECONDS)).isNull(); // no turn was sent
        assertThat(auditLog().events().getLast().detail()).containsEntry("denied_kbs", List.of(SLUG));
    }

    @Test
    void theAgentsKbRefusalIsRelayedAs403KbForbidden() throws Exception {
        AGENT.enqueue(json(200, "{\"live\":[\"" + SLUG + "\"]}"));
        AGENT.enqueue(json(403, "{\"request_id\":\"r\",\"error\":{\"code\":\"kb_forbidden\",\"message_ar\":\"x\","
                + "\"message_en\":\"x\"}}"));
        mvc.perform(chat(withRoles("user", "cap:general", "cap:kb", "kb:" + SLUG + ":read"), List.of(SLUG)))
                .andExpect(status().isForbidden()).andExpect(jsonPath("$.error.code").value("kb_forbidden"));
        assertThat(auditLog().events().getLast().status()).isEqualTo(AuditStatus.DENIED);
    }

    @Test
    void theComposersListIsTheAgentsFilteredByTheTokensPermissions() throws Exception {
        AGENT.enqueue(json(200, "{\"capabilities\":[{\"slug\":\"kb\",\"name\":{\"ar\":\"قواعد المعرفة\",\"en\":\"KBs\"},"
                + "\"description\":{\"ar\":\"و\",\"en\":\"d\"},\"icon\":\"library\",\"ui\":{\"mode\":\"auto\",\"order\":25}}],"
                + "\"knowledge_bases\":[{\"slug\":\"" + SLUG + "\",\"name\":{\"ar\":\"الاقتصاد الدولي\",\"en\":\"IE\"},"
                + "\"description\":{\"ar\":\"\",\"en\":\"\"},\"icon\":\"library\"}]}"));
        mvc.perform(get("/v1/capabilities").header(HttpHeaders.AUTHORIZATION,
                        bearer(withRoles("user", "cap:general", "cap:kb", "kb:" + SLUG + ":read", "admin"))))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.knowledge_bases[0].slug").value(SLUG))
                .andExpect(jsonPath("$.data.capabilities[0].ui.mode").value("auto"));
        RecordedRequest sent = AGENT.takeRequest(5, TimeUnit.SECONDS);
        assertThat(sent.getPath()).isEqualTo("/v1/capabilities");
        // Only cap:*, kb:*:read and skill:* leave the gateway; admin is not a permission (ADR-0006).
        assertThat(sent.getHeader("X-User-Permissions")).isEqualTo("cap:general,cap:kb,kb:" + SLUG + ":read");
        assertThat(sent.getHeader("X-Tenant-Id")).isEqualTo("tenant-a");
    }

    // ------------------------------------------------------------------ create, role, delete

    @Test
    void createMakesTheRealmRoleFirstThenRecordsTheKbAndAuditsTheActor() throws Exception {
        KEYCLOAK_ADMIN.enqueue(json(200, "{\"access_token\":\"svc\"}"));
        KEYCLOAK_ADMIN.enqueue(new MockResponse().setResponseCode(201));
        AGENT.enqueue(json(201, KB_VIEW));
        mvc.perform(createRequest(admin()))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.data.role").value("kb:" + SLUG + ":read"));
        KEYCLOAK_ADMIN.takeRequest(5, TimeUnit.SECONDS); // the token
        RecordedRequest role = KEYCLOAK_ADMIN.takeRequest(5, TimeUnit.SECONDS);
        assertThat(role.getPath()).isEqualTo("/admin/realms/agent/roles");
        assertThat(mapper.readTree(role.getBody().readUtf8()).path("name").asText()).isEqualTo("kb:" + SLUG + ":read");
        RecordedRequest agentCall = AGENT.takeRequest(5, TimeUnit.SECONDS);
        assertThat(agentCall.getPath()).isEqualTo("/v1/admin/kbs");
        assertThat(agentCall.getHeader("X-User-Admin")).isEqualTo("true");
        JsonNode sent = mapper.readTree(agentCall.getBody().readUtf8());
        assertThat(sent.path("slug").asText()).isEqualTo(SLUG);
        assertThat(sent.path("name").path("ar").asText()).isEqualTo("الاقتصاد الدولي");
        AuditEvent event = auditLog().events().getLast();
        assertThat(event.action()).isEqualTo("kb.create");
        assertThat(event.status()).isEqualTo(AuditStatus.OK);
        assertThat(event.detail()).containsEntry("role_created", true).containsEntry("actor_username", "admin")
                .containsEntry("actor_name", "مدير").containsEntry("kb", SLUG);
    }

    @Test
    void anExistingRoleConvergesAndATakenSlugIsKbExists() throws Exception {
        KEYCLOAK_ADMIN.enqueue(json(200, "{\"access_token\":\"svc\"}"));
        KEYCLOAK_ADMIN.enqueue(new MockResponse().setResponseCode(409));
        AGENT.enqueue(json(409, "{\"request_id\":\"r\",\"error\":{\"code\":\"kb_exists\",\"message_ar\":\"x\",\"message_en\":\"x\"}}"));
        mvc.perform(createRequest(admin()))
                .andExpect(status().isConflict()).andExpect(jsonPath("$.error.code").value("kb_exists"));
        AuditEvent event = auditLog().events().getLast();
        assertThat(event.status()).isEqualTo(AuditStatus.DENIED);
        assertThat(event.detail()).containsEntry("role_created", false).containsEntry("error_code", "kb_exists");
    }

    @Test
    void keycloakFailingIs502IdentityUnavailableAndTheAgentRecordsNothing() throws Exception {
        KEYCLOAK_ADMIN.enqueue(json(500, "{}"));
        int before = AGENT.getRequestCount();
        mvc.perform(createRequest(admin()))
                .andExpect(status().isBadGateway()).andExpect(jsonPath("$.error.code").value("identity_unavailable"));
        assertThat(AGENT.getRequestCount()).isEqualTo(before);
        assertThat(auditLog().events().getLast().status()).isEqualTo(AuditStatus.ERROR);
    }

    @Test
    void aHungKeycloakEndsTheCreateWithinItsBoundWhileAnotherUsersRequestGoesOn() throws Exception {
        // Rule 12h: a hung dependency while another user's request runs.
        KEYCLOAK_ADMIN.enqueue(json(200, "{\"access_token\":\"svc\"}").setHeadersDelay(30, TimeUnit.SECONDS));
        long started = System.nanoTime();
        CompletableFuture<Integer> create = CompletableFuture.supplyAsync(() -> {
            try {
                return mvc.perform(createRequest(admin())).andReturn().getResponse().getStatus();
            } catch (Exception e) {
                throw new IllegalStateException(e);
            }
        });
        Thread.sleep(300);
        AGENT.enqueue(json(200, "{\"capabilities\":[],\"knowledge_bases\":[]}"));
        long other = System.nanoTime();
        mvc.perform(get("/v1/capabilities").header(HttpHeaders.AUTHORIZATION, bearer(withRoles("user", "cap:general"))))
                .andExpect(status().isOk());
        assertThat(Duration.ofNanos(System.nanoTime() - other)).isLessThan(Duration.ofSeconds(1));
        assertThat(create.get(15, TimeUnit.SECONDS)).isEqualTo(502);
        assertThat(Duration.ofNanos(System.nanoTime() - started)).isLessThan(Duration.ofSeconds(11));
        KEYCLOAK_ADMIN.takeRequest(1, TimeUnit.SECONDS);
    }

    @Test
    void deleteRelaysWhetherTheIndexWasPurged() throws Exception {
        AGENT.enqueue(json(200, "{\"slug\":\"" + SLUG + "\",\"role\":\"kb:" + SLUG + ":read\",\"index_purged\":true}"));
        mvc.perform(delete("/v1/admin/kbs/" + SLUG).header(HttpHeaders.AUTHORIZATION, bearer(admin())))
                .andExpect(status().isOk()).andExpect(jsonPath("$.data.index_purged").value(true));
        assertThat(AGENT.takeRequest(5, TimeUnit.SECONDS).getMethod()).isEqualTo("DELETE");
        assertThat(auditLog().events().getLast().detail()).containsEntry("index_purged", true);
        AGENT.enqueue(json(404, "{\"request_id\":\"r\",\"error\":{\"code\":\"kb_not_found\",\"message_ar\":\"x\",\"message_en\":\"x\"}}"));
        mvc.perform(delete("/v1/admin/kbs/" + SLUG).header(HttpHeaders.AUTHORIZATION, bearer(admin())))
                .andExpect(status().isNotFound()).andExpect(jsonPath("$.error.code").value("kb_not_found"));
    }

    // ------------------------------------------------------------------ documents

    @Test
    void anUploadIsStoredUnderTheKbAndRegisteredWithItsMetadata() throws Exception {
        AGENT.enqueue(json(201, DOCUMENT_VIEW));
        mvc.perform(upload(admin(), "C:\\docs\\weo.pdf", "2026-07-15"))
                .andExpect(status().isCreated()).andExpect(jsonPath("$.data.status").value("pending"));
        RecordedRequest registered = AGENT.takeRequest(5, TimeUnit.SECONDS);
        assertThat(registered.getPath()).isEqualTo("/v1/admin/kbs/" + SLUG + "/documents");
        JsonNode sent = mapper.readTree(registered.getBody().readUtf8());
        assertThat(sent.path("name").asText()).isEqualTo("weo.pdf");
        assertThat(sent.path("object_key").asText()).startsWith("kb/" + SLUG + "/");
        assertThat(sent.path("metadata").path("publisher").asText()).isEqualTo("صندوق النقد الدولي");
        assertThat(sent.path("metadata").path("published_on").asText()).isEqualTo("2026-07-15");
        assertThat(objects().objects).containsKey(sent.path("object_key").asText());
        AuditEvent event = auditLog().events().getLast();
        assertThat(event.action()).isEqualTo("kb.document.upload");
        assertThat(event.detail()).containsEntry("document_id", "6f1c0a2e-3b4d-4e5f-8a9b-0c1d2e3f4a5b");
    }

    @Test
    void aRefusedRegistrationLeavesNoObjectAndBadMetadataIs400() throws Exception {
        AGENT.enqueue(json(409, "{\"request_id\":\"r\",\"error\":{\"code\":\"kb_supersedes_invalid\",\"message_ar\":\"x\","
                + "\"message_en\":\"x\",\"details\":[\"a loop\"]}}"));
        mvc.perform(upload(admin(), "weo.pdf", "2026-07-15"))
                .andExpect(status().isConflict()).andExpect(jsonPath("$.error.code").value("kb_supersedes_invalid"));
        assertThat(objects().objects).isEmpty();
        int before = AGENT.getRequestCount();
        mvc.perform(upload(admin(), "weo.pdf", "15/07/2026")).andExpect(status().isBadRequest());
        mvc.perform(upload(admin(), "weo.exe", "2026-07-15")).andExpect(status().isUnsupportedMediaType());
        assertThat(AGENT.getRequestCount()).isEqualTo(before);
    }

    private MockHttpServletRequestBuilder chat(String token, List<String> kbs) throws Exception {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("conversation_id", CONVERSATION_ID);
        body.put("message", "كم النمو المتوقع؟");
        body.put("selected_kbs", kbs);
        return post("/v1/chat/turns").header(HttpHeaders.AUTHORIZATION, bearer(token))
                .contentType(MediaType.APPLICATION_JSON).accept(MediaType.TEXT_EVENT_STREAM)
                .characterEncoding(StandardCharsets.UTF_8).content(mapper.writeValueAsString(body));
    }
}
