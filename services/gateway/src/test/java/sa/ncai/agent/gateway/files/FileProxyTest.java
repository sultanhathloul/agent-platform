package sa.ncai.agent.gateway.files;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.util.List;
import java.util.concurrent.TimeUnit;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpHeaders;
import sa.ncai.agent.gateway.GatewayTestSupport;
import sa.ncai.agent.gateway.audit.AuditEvent;
import sa.ncai.agent.gateway.audit.AuditStatus;

/**
 * The page «ملفاتي» (my-files PR, owner decision 1, ADR-0442): the agent lists the caller's own files, and the
 * gateway keeps only those whose conversation its own table says is the caller's, so another user's file never
 * leaves even if the agent erred. Audited as file.library with the filters and the count, never the query.
 */
class FileProxyTest extends GatewayTestSupport {

    private static final String CONVERSATION = "6a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d";
    private static final String STRANGER = "7b2c3d4e-5f6a-4b7c-9d8e-0f1a2b3c4d5e";

    @BeforeEach
    void ownTheConversation() {
        conversations.bindOrGetOwner(CONVERSATION, "user-123", "tenant-a");
        conversations.bindOrGetOwner(STRANGER, "user-456", "tenant-a");
    }

    private static MockResponse json(int status, String body) {
        return new MockResponse().setResponseCode(status).setHeader("Content-Type", "application/json").setBody(body);
    }

    private static String item(String id, String conversation, String name) {
        return """
                {"id":"%s","conversation_id":"%s","conversation_title":"هذا تحليل الدم","conversation_created_at":\
                "2026-11-01T08:00:00Z","name":"%s","mime":"application/pdf","kind":"pdf","size":2048,\
                "status":"indexed","reason":null,"pages":2,"created_at":"2026-11-01T08:01:00Z"}"""
                .formatted(id, conversation, name);
    }

    private AuditEvent singleAudit() {
        List<AuditEvent> events = auditLog().events();
        assertThat(events).hasSize(1);
        return events.getFirst();
    }

    @Test
    void theLibraryListsOnlyFilesOfTheCallersConversationsAndAuditsNoQueryText() throws Exception {
        AGENT.enqueue(json(200, "{\"files\":[" + item("0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d", CONVERSATION,
                "تحليل-الدم.pdf") + "," + item("1b2c3d4e-5f6a-4b7c-8d9e-0f1a2b3c4d5e", STRANGER, "سري.pdf")
                + "],\"next_before\":null}"));

        mvc.perform(get("/v1/files").param("q", "تحليل الدم").param("kind", "pdf").param("kind", "docx")
                        .param("status", "indexed").param("since", "2026-10-01T00:00:00Z")
                        .header(HttpHeaders.AUTHORIZATION, bearer(token())).header("X-User-Id", "victim"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.files.length()").value(1))
                .andExpect(jsonPath("$.data.files[0].name").value("تحليل-الدم.pdf"))
                .andExpect(jsonPath("$.data.files[0].conversation_id").value(CONVERSATION))
                .andExpect(jsonPath("$.data.files[0].conversation_title").value("هذا تحليل الدم"))
                .andExpect(jsonPath("$.data.files[0].object_key").doesNotExist());

        RecordedRequest upstream = AGENT.takeRequest(5, TimeUnit.SECONDS);
        assertThat(upstream.getRequestUrl().encodedPath()).isEqualTo("/v1/files");
        assertThat(upstream.getRequestUrl().queryParameter("q")).isEqualTo("تحليل الدم");
        assertThat(upstream.getRequestUrl().queryParameterValues("kind")).containsExactly("pdf", "docx");
        assertThat(upstream.getRequestUrl().queryParameter("status")).isEqualTo("indexed");
        assertThat(upstream.getRequestUrl().queryParameter("limit")).isEqualTo("30");
        // The caller is the verified token's, never a header the client sent.
        assertThat(upstream.getHeaders().values("X-User-Id")).containsExactly("user-123");
        assertThat(upstream.getHeaders().values("X-Tenant-Id")).containsExactly("tenant-a");
        AuditEvent event = singleAudit();
        assertThat(event.action()).isEqualTo("file.library");
        assertThat(event.status()).isEqualTo(AuditStatus.OK);
        assertThat(event.detail()).containsEntry("files", 1).containsEntry("dropped", 1).containsEntry("query_chars", 10)
                .containsEntry("status", "indexed").containsEntry("dated", true);
        assertThat(event.detail().toString()).doesNotContain("تحليل").doesNotContain("سري");
    }

    @Test
    void theLibraryRefusesMalformedFiltersBeforeTheAgentAndWithoutAnAuditRow() throws Exception {
        for (String[] bad : List.of(new String[] {"kind", "exe"}, new String[] {"status", "deleted"},
                new String[] {"since", "yesterday"}, new String[] {"limit", "1000"},
                new String[] {"q", "س".repeat(201)})) {
            mvc.perform(get("/v1/files").param(bad[0], bad[1]).header(HttpHeaders.AUTHORIZATION, bearer(token())))
                    .andExpect(status().isBadRequest())
                    .andExpect(jsonPath("$.error.code").value("invalid_request"));
        }
        assertThat(AGENT.takeRequest(200, TimeUnit.MILLISECONDS)).isNull();
        assertThat(auditLog().events()).isEmpty();
    }

    @Test
    void theLibraryRequiresAuthenticationAndSaysWhenTheAgentIsDown() throws Exception {
        mvc.perform(get("/v1/files")).andExpect(status().isUnauthorized());
        AGENT.enqueue(json(500, "{}"));
        mvc.perform(get("/v1/files").header(HttpHeaders.AUTHORIZATION, bearer(token())))
                .andExpect(status().isBadGateway())
                .andExpect(jsonPath("$.error.code").value("agent_unavailable"));
        assertThat(singleAudit().status()).isEqualTo(AuditStatus.ERROR);
    }
}
