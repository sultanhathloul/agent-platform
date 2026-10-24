package sa.ncai.agent.gateway.settings;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.TimeUnit;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;
import sa.ncai.agent.gateway.GatewayTestSupport;
import sa.ncai.agent.gateway.InMemoryAuditRepository;
import sa.ncai.agent.gateway.audit.AuditEvent;
import sa.ncai.agent.gateway.audit.AuditStatus;

/**
 * The platform settings page's API (ADR-0276 to ADR-0284): admin only, the admin role forwarded to
 * the agent, which checks it too, and every call audited fail-closed; a save's record holds each
 * value it changed, before and after, with the actor's username and display name.
 */
class AdminSettingsProxyTest extends GatewayTestSupport {

    private static final String VIEW = """
            {"version":4,"ignored":[],"fields":[{"key":"turn.per_user_max","group":"turns","unit":"count",\
            "integer":true,"nullable":false,"per_tenant":false,"minimum":1,"maximum":20,"default":2,"value":1,\
            "overridden":true,"source":"config/turn.yaml per_user_max","server":null}],\
            "read_only":[{"key":"lock_pool_size","value":20,"unit":"count","source":".env LOCK_POOL_SIZE"}],\
            "tree":[{"rule":"answer-reserve-in-turn","family":"answer-reserve-in-turn","params":{},\
            "inner":"answer_reserve_s","inner_value":45,"outer":"deadline_s","outer_value":120,"strict":true,\
            "holds":true}]}""";
    private static final String SAVED = "{\"version\":5,\"change\":\"save\",\"reverted_to\":null,"
            + "\"changes\":[{\"key\":\"turn.per_user_max\",\"before\":2,\"after\":1}],\"settings\":" + VIEW + "}";
    private static final String USAGE = """
            {"turns":{"in_flight":3,"capacity":20,"per_user_max":2,"top_user_in_flight":2,\
            "tenants":[{"tenant_id":"default","in_flight":3,"limit":null}]},\
            "mcp":[{"id":"rag","max_concurrency":6,"file_max_concurrency":8,"in_use":1,"background_max":3,\
            "background_in_use":0,"breaker":"open","open_for_s":12.5}],\
            "registrations_in_background":1,"tasks_abandoned":0}""";

    @Autowired
    ObjectMapper mapper;

    private static String adminToken() {
        return token(claims -> claims.claim("realm_access", Map.of("roles", List.of("user", "admin", "cap:general"))));
    }

    private static MockHttpServletRequestBuilder call(String method, String path) {
        return switch (method) {
            case "GET" -> get(path);
            default -> post(path).contentType(MediaType.APPLICATION_JSON).content(switch (path) {
                case "/v1/admin/settings/revert" -> "{\"version\":3}";
                default -> "{\"overrides\":{\"turn.per_user_max\":1}}";
            });
        };
    }

    @ParameterizedTest
    @CsvSource({
        "GET,/v1/admin/settings,settings.get",
        "POST,/v1/admin/settings/preview,settings.preview",
        "POST,/v1/admin/settings,settings.save",
        "POST,/v1/admin/settings/revert,settings.revert",
        "GET,/v1/admin/settings/versions,settings.versions",
        "GET,/v1/admin/usage,settings.usage"
    })
    void aNonAdminIsRefusedAuditedAndTheAgentNeverCalled(String method, String path, String action) throws Exception {
        int before = AGENT.getRequestCount();
        mvc.perform(call(method, path).header(HttpHeaders.AUTHORIZATION, bearer(token())))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error.code").value("forbidden"));
        assertThat(AGENT.getRequestCount()).isEqualTo(before);
        AuditEvent event = singleAudit();
        assertThat(event.action()).isEqualTo(action);
        assertThat(event.status()).isEqualTo(AuditStatus.DENIED);
        assertThat(event.detail()).containsEntry("error_code", "forbidden").containsEntry("actor_username", "analyst");
    }

    @Test
    void anAdminReadsTheSettingsWithTheAdminRoleForwarded() throws Exception {
        AGENT.enqueue(json(200, VIEW));
        mvc.perform(get("/v1/admin/settings").header(HttpHeaders.AUTHORIZATION, bearer(adminToken())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.version").value(4))
                .andExpect(jsonPath("$.data.fields[0].default").value(2))
                .andExpect(jsonPath("$.data.fields[0].value").value(1))
                .andExpect(jsonPath("$.data.read_only[0].key").value("lock_pool_size"))
                .andExpect(jsonPath("$.data.tree[0].holds").value(true));
        RecordedRequest upstream = AGENT.takeRequest(5, TimeUnit.SECONDS);
        assertThat(upstream.getPath()).isEqualTo("/v1/admin/settings");
        assertThat(upstream.getHeader("X-User-Admin")).isEqualTo("true");
        assertThat(upstream.getHeader("X-User-Id")).isEqualTo("user-123");
        assertThat(upstream.getHeader("X-User-Name")).isEqualTo("analyst");
        assertThat(singleAudit().status()).isEqualTo(AuditStatus.OK);
    }

    @Test
    void aSaveWritesOneAuditRowWithBeforeAfterAndTheActorsNames() throws Exception {
        AGENT.enqueue(json(201, SAVED));
        mvc.perform(post("/v1/admin/settings")
                        .header(HttpHeaders.AUTHORIZATION, bearer(adminToken()))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"overrides\":{\"turn.per_user_max\":1},\"expected_version\":4}"))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.data.version").value(5))
                .andExpect(jsonPath("$.data.changes[0].after").value(1));
        RecordedRequest upstream = AGENT.takeRequest(5, TimeUnit.SECONDS);
        JsonNode sent = mapper.readTree(upstream.getBody().readUtf8());
        assertThat(sent.path("overrides").path("turn.per_user_max").asInt()).isEqualTo(1);
        assertThat(sent.path("expected_version").asInt()).isEqualTo(4);
        AuditEvent event = singleAudit();
        assertThat(event.action()).isEqualTo("settings.save");
        assertThat(event.status()).isEqualTo(AuditStatus.OK);
        assertThat(event.detail())
                .containsEntry("version", 5L)
                .containsEntry("change", "save")
                .containsEntry("actor_username", "analyst")
                .containsEntry("actor_name", "سارة المحللة");
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> changes = (List<Map<String, Object>>) event.detail().get("changes");
        assertThat(changes).hasSize(1);
        assertThat(changes.getFirst()).containsEntry("key", "turn.per_user_max");
        assertThat(((JsonNode) changes.getFirst().get("before")).asInt()).isEqualTo(2);
        assertThat(((JsonNode) changes.getFirst().get("after")).asInt()).isEqualTo(1);
    }

    @Test
    void anInvalidSaveReachesTheAdminInPlainWordsAndIsAudited() throws Exception {
        AGENT.enqueue(json(400, """
                {"request_id":"r","error":{"code":"settings_invalid",\
                "message_ar":"مهلة الإجابة (130 ثانية) أطول من مهلة السؤال كله (120 ثانية) أو تساويها، ويجب أن تكون أقصر منها.",\
                "message_en":"The time kept for the answer (130 s) is not shorter than the whole-question time limit (120 s).",\
                "details":["answer-reserve-in-turn"]}}"""));
        mvc.perform(post("/v1/admin/settings")
                        .header(HttpHeaders.AUTHORIZATION, bearer(adminToken()))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"overrides\":{\"turn.answer_reserve_s\":130}}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error.code").value("settings_invalid"))
                .andExpect(jsonPath("$.error.message_ar").value(
                        "مهلة الإجابة (130 ثانية) أطول من مهلة السؤال كله (120 ثانية) أو تساويها، ويجب أن تكون أقصر منها."))
                .andExpect(jsonPath("$.error.details[0]").value("answer-reserve-in-turn"));
        AuditEvent event = singleAudit();
        assertThat(event.status()).isEqualTo(AuditStatus.ERROR);
        assertThat(event.detail()).containsEntry("error_code", "settings_invalid")
                .containsEntry("details", List.of("answer-reserve-in-turn"));
    }

    @Test
    void anMcpLimitAboveTheFilesIsRefusedAsTheAgentSaid() throws Exception {
        AGENT.enqueue(json(400, """
                {"request_id":"r","error":{"code":"settings_invalid","message_ar":"س","message_en":"x",\
                "details":["range:mcp.rag.max_concurrency"]}}"""));
        mvc.perform(post("/v1/admin/settings")
                        .header(HttpHeaders.AUTHORIZATION, bearer(adminToken()))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"overrides\":{\"mcp.rag.max_concurrency\":9}}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error.details[0]").value("range:mcp.rag.max_concurrency"));
    }

    @Test
    void aConflictingSaveAndAMissingVersionKeepTheirCodes() throws Exception {
        AGENT.enqueue(json(409, "{\"request_id\":\"r\",\"error\":{\"code\":\"settings_conflict\",\"message_ar\":\"x\",\"message_en\":\"x\"}}"));
        mvc.perform(post("/v1/admin/settings")
                        .header(HttpHeaders.AUTHORIZATION, bearer(adminToken()))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"overrides\":{},\"expected_version\":1}"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.error.code").value("settings_conflict"));
        auditLog().clear();
        AGENT.enqueue(json(404, "{\"request_id\":\"r\",\"error\":{\"code\":\"settings_version_not_found\",\"message_ar\":\"x\",\"message_en\":\"x\"}}"));
        mvc.perform(post("/v1/admin/settings/revert")
                        .header(HttpHeaders.AUTHORIZATION, bearer(adminToken()))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"version\":99}"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error.code").value("settings_version_not_found"));
    }

    @Test
    void theUsageIsCountsOnlyAndAudited() throws Exception {
        String session = UUID.randomUUID().toString();
        AGENT.enqueue(json(200, USAGE));
        mvc.perform(get("/v1/admin/usage").param("page_session", session)
                        .header(HttpHeaders.AUTHORIZATION, bearer(adminToken())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.turns.in_flight").value(3))
                .andExpect(jsonPath("$.data.mcp[0].breaker").value("open"))
                .andExpect(jsonPath("$.data.mcp[0].open_for_s").value(12.5));
        assertThat(singleAudit().detail()).containsEntry("turns_in_flight", 3).containsEntry("page_session", session);
    }

    @Test
    void aPageSessionsFirstUsageReadIsAuditedAndItsNextReadsAreNot() throws Exception {
        String first = UUID.randomUUID().toString();
        for (int read = 0; read < 3; read++) {
            AGENT.enqueue(json(200, USAGE));
            mvc.perform(get("/v1/admin/usage").param("page_session", first)
                            .header(HttpHeaders.AUTHORIZATION, bearer(adminToken())))
                    .andExpect(status().isOk())
                    .andExpect(jsonPath("$.data.turns.in_flight").value(3));
        }
        assertThat(singleAudit().detail()).containsEntry("page_session", first);
        // Another page load is a new page session: its first read is audited too.
        String second = UUID.randomUUID().toString();
        AGENT.enqueue(json(200, USAGE));
        mvc.perform(get("/v1/admin/usage").param("page_session", second)
                        .header(HttpHeaders.AUTHORIZATION, bearer(adminToken())))
                .andExpect(status().isOk());
        assertThat(auditLog().events()).hasSize(2);
        assertThat(auditLog().events().get(1).detail()).containsEntry("page_session", second);
    }

    @Test
    void aMalformedPageSessionIsRefusedBeforeTheAgent() throws Exception {
        int before = AGENT.getRequestCount();
        mvc.perform(get("/v1/admin/usage").param("page_session", "not a session!")
                        .header(HttpHeaders.AUTHORIZATION, bearer(adminToken())))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error.code").value("invalid_request"));
        assertThat(AGENT.getRequestCount()).isEqualTo(before);
    }

    @Test
    void everySaveIsAuditedEvenWithinTheUsageInterval() throws Exception {
        for (int save = 0; save < 3; save++) {
            AGENT.enqueue(json(201, SAVED));
            mvc.perform(post("/v1/admin/settings")
                            .header(HttpHeaders.AUTHORIZATION, bearer(adminToken()))
                            .contentType(MediaType.APPLICATION_JSON)
                            .content("{\"overrides\":{\"turn.per_user_max\":1}}"))
                    .andExpect(status().isCreated());
        }
        assertThat(auditLog().events()).hasSize(3).allSatisfy(event -> {
            assertThat(event.action()).isEqualTo("settings.save");
            assertThat(event.status()).isEqualTo(AuditStatus.OK);
        });
    }

    @Test
    void withTheAuditLogDownNothingReachesTheAgent() throws Exception {
        auditLog().setMode(InMemoryAuditRepository.Mode.DOWN);
        int before = AGENT.getRequestCount();
        mvc.perform(post("/v1/admin/settings")
                        .header(HttpHeaders.AUTHORIZATION, bearer(adminToken()))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"overrides\":{\"turn.per_user_max\":1}}"))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.error.code").value("audit_unavailable"));
        assertThat(AGENT.getRequestCount()).isEqualTo(before);
    }

    @Test
    void aSaveWhoseRecordFailsIsAnsweredAuditUnavailable() throws Exception {
        AGENT.enqueue(new MockResponse().setResponseCode(201).setHeader("Content-Type", "application/json").setBody(SAVED));
        auditLog().setMode(InMemoryAuditRepository.Mode.WRITES_FAIL);
        mvc.perform(post("/v1/admin/settings")
                        .header(HttpHeaders.AUTHORIZATION, bearer(adminToken()))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"overrides\":{\"turn.per_user_max\":1}}"))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.error.code").value("audit_unavailable"));
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
