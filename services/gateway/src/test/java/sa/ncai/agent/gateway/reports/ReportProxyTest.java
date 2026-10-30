package sa.ncai.agent.gateway.reports;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.List;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpHeaders;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.test.context.bean.override.convention.TestBean;
import sa.ncai.agent.gateway.GatewayTestSupport;
import sa.ncai.agent.gateway.audit.AuditEvent;
import sa.ncai.agent.gateway.audit.AuditStatus;

/**
 * The report API (reports PR, I4 to I6): only the conversation's owner reads a report, checked by the
 * agent (the caller it is named) and again by the gateway (its conversations table); a missing report and
 * another owner's are the same 404. The page reaches the panel's frame by a signed 60 s view link and
 * leaves under the CSP of I4 with no X-Frame-Options and no CORS headers; the download is an attachment
 * under {@code sandbox}; the kit serves only what report.yaml lists. Every read is audited, content-free.
 */
class ReportProxyTest extends GatewayTestSupport {

    private static final String REPORT = "5d0c6a3e-1f2b-4c3d-8e4f-5a6b7c8d9e0f";
    private static final String CONVERSATION = "6a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d";
    private static final String TITLE = "لوحة مؤشرات التضخم";
    private static final String PAGE = "<!doctype html><html dir=\"rtl\"><body><h1>" + TITLE + "</h1></body></html>";
    private static final String KIT = "http://localhost:8080/report-kit/v1/";
    private static final String CSP = "default-src 'none'; script-src " + KIT + "; style-src " + KIT
            + " 'unsafe-inline'; font-src " + KIT + "; img-src data:; connect-src 'none'; frame-src 'none'; "
            + "form-action 'none'; base-uri 'none'; frame-ancestors http://localhost:3000; sandbox allow-scripts";

    private static final String VIEW = """
            {"id":"%s","conversation_id":"%s","title":"%s","latest_version":2,\
            "created_at":"2026-10-28T08:00:00Z","updated_at":"2026-10-28T08:05:00Z","versions":[\
            {"version":1,"title":"%s","size":2048,"locale":"ar","created_at":"2026-10-28T08:00:00Z","figures":4,"general":1},\
            {"version":2,"title":"%s","size":2100,"locale":"ar","created_at":"2026-10-28T08:05:00Z","figures":5,"general":0}]}"""
            .formatted(REPORT, CONVERSATION, TITLE, TITLE, TITLE);

    /** The view tickets' clock, moved by the expiry test. */
    static final AtomicReference<Instant> NOW = new AtomicReference<>(Instant.now());

    @TestBean(methodName = "controlledTickets")
    ViewTickets tickets;

    static ViewTickets controlledTickets() {
        return new ViewTickets(new Clock() {
            @Override
            public ZoneOffset getZone() {
                return ZoneOffset.UTC;
            }

            @Override
            public Clock withZone(java.time.ZoneId zone) {
                return this;
            }

            @Override
            public Instant instant() {
                return NOW.get();
            }
        });
    }

    @Autowired
    ObjectMapper mapper;

    @BeforeEach
    void ownTheConversation() {
        NOW.set(Instant.now());
        conversations.bindOrGetOwner(CONVERSATION, "user-123", "tenant-a");
    }

    private static MockResponse json(int status, String body) {
        return new MockResponse().setResponseCode(status).setHeader("Content-Type", "application/json").setBody(body);
    }

    private static MockResponse notFound() {
        return json(404, "{\"request_id\":\"x\",\"error\":{\"code\":\"report_not_found\",\"message_ar\":\"م\","
                + "\"message_en\":\"m\",\"retryable\":false}}");
    }

    private static String otherUser() {
        return token(claims -> claims.subject("user-456").claim("preferred_username", "other"));
    }

    private AuditEvent singleAudit() {
        List<AuditEvent> events = auditLog().events();
        assertThat(events).hasSize(1);
        return events.getFirst();
    }

    @Test
    void theOwnerReadsTheReportAsTheAgentAnswersItAndTheReadIsAuditedWithoutContent() throws Exception {
        AGENT.enqueue(json(200, VIEW));

        mvc.perform(get("/v1/reports/" + REPORT.toUpperCase()).header(HttpHeaders.AUTHORIZATION, bearer(token()))
                        .header("X-User-Id", "victim"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.id").value(REPORT))
                .andExpect(jsonPath("$.data.conversation_id").value(CONVERSATION))
                .andExpect(jsonPath("$.data.latest_version").value(2))
                .andExpect(jsonPath("$.data.versions[0].general").value(1))
                .andExpect(jsonPath("$.data.versions[1].figures").value(5));

        RecordedRequest upstream = AGENT.takeRequest(5, TimeUnit.SECONDS);
        assertThat(upstream.getPath()).isEqualTo("/v1/reports/" + REPORT);
        assertThat(upstream.getHeader("X-Internal-Token")).isEqualTo(INTERNAL_TOKEN);
        assertThat(upstream.getHeaders().values("X-User-Id")).containsExactly("user-123");
        assertThat(upstream.getHeaders().values("X-Tenant-Id")).containsExactly("tenant-a");
        AuditEvent event = singleAudit();
        assertThat(event.action()).isEqualTo("report.read");
        assertThat(event.status()).isEqualTo(AuditStatus.OK);
        assertThat(event.conversationId()).isEqualTo(CONVERSATION);
        assertThat(event.detail()).containsEntry("report_id", REPORT);
        assertThat(event.detail().toString()).doesNotContain("التضخم");
    }

    @Test
    void anotherUsersReportIs404AtEveryEndpoint() throws Exception {
        String other = otherUser();
        // The agent finds none of the caller's (its own check)...
        List<org.springframework.test.web.servlet.RequestBuilder> reads = List.of(
                get("/v1/reports/" + REPORT).header(HttpHeaders.AUTHORIZATION, bearer(other)),
                get("/v1/reports/" + REPORT + "/versions/1").header(HttpHeaders.AUTHORIZATION, bearer(other)),
                post("/v1/reports/" + REPORT + "/versions/1/view").header(HttpHeaders.AUTHORIZATION, bearer(other)),
                get("/v1/reports/" + REPORT + "/versions/1/download").header(HttpHeaders.AUTHORIZATION, bearer(other)));
        for (org.springframework.test.web.servlet.RequestBuilder read : reads) {
            auditLog().clear();
            AGENT.enqueue(notFound());
            mvc.perform(read)
                    .andExpect(status().isNotFound())
                    .andExpect(jsonPath("$.error.code").value("report_not_found"))
                    .andExpect(jsonPath("$.error.message_ar").value("التقرير غير موجود أو لا تملك صلاحية الاطلاع عليه."));
            assertThat(AGENT.takeRequest(5, TimeUnit.SECONDS).getHeader("X-User-Id")).isEqualTo("user-456");
            AuditEvent event = singleAudit();
            assertThat(event.status()).isEqualTo(AuditStatus.DENIED);
            assertThat(event.detail()).containsEntry("error_code", "report_not_found");
        }
        // When the agent did answer, the gateway's own check refuses all the same (the next test).
    }

    private static String versionJson() {
        return """
                {"id":"%s","conversation_id":"%s","version":1,"title":"%s","size":2048,"locale":"ar",\
                "created_at":"2026-10-28T08:00:00Z","figures":4,"general":1,\
                "citations":[{"id":"c1","title":"آفاق الاقتصاد","source":"kb","kb":"imf","document_id":"d1","pages":[3],\
                "kb_name":{"ar":"صندوق النقد","en":"IMF"},"publisher":"IMF","published_on":"2026-07-01"}],"html":%s}"""
                .formatted(REPORT, CONVERSATION, TITLE, "\"" + PAGE.replace("\"", "\\\"") + "\"");
    }

    @Test
    void theGatewaysOwnCheckRefusesAReportOfAConversationNotTheCallersEvenWhenTheAgentAnswers() throws Exception {
        ((sa.ncai.agent.gateway.InMemoryConversationRepository) conversations).clear();
        conversations.bindOrGetOwner(CONVERSATION, "user-456", "tenant-a");
        AGENT.enqueue(json(200, VIEW));
        mvc.perform(get("/v1/reports/" + REPORT).header(HttpHeaders.AUTHORIZATION, bearer(token())))
                .andExpect(status().isNotFound()).andExpect(jsonPath("$.error.code").value("report_not_found"));
        AGENT.enqueue(json(200, versionJson()));
        mvc.perform(get("/v1/reports/" + REPORT + "/versions/1").header(HttpHeaders.AUTHORIZATION, bearer(token())))
                .andExpect(status().isNotFound()).andExpect(jsonPath("$.error.code").value("report_not_found"));
        AGENT.enqueue(json(200, VIEW));
        mvc.perform(post("/v1/reports/" + REPORT + "/versions/1/view").header(HttpHeaders.AUTHORIZATION, bearer(token())))
                .andExpect(status().isNotFound()).andExpect(jsonPath("$.error.code").value("report_not_found"));
        AGENT.enqueue(json(200, VIEW));
        mvc.perform(get("/v1/reports/" + REPORT + "/versions/1/download")
                        .header(HttpHeaders.AUTHORIZATION, bearer(token())))
                .andExpect(status().isNotFound()).andExpect(jsonPath("$.error.code").value("report_not_found"));
        // One agent call each: the download's page was never fetched.
        for (int i = 0; i < 4; i++) {
            assertThat(AGENT.takeRequest(5, TimeUnit.SECONDS).getPath()).doesNotContain("/page");
        }
        assertThat(AGENT.takeRequest(200, TimeUnit.MILLISECONDS)).isNull();
        assertThat(auditLog().events()).hasSize(4)
                .allSatisfy(e -> assertThat(e.status()).isEqualTo(AuditStatus.DENIED));
    }

    @Test
    void aConversationsReportsAreListedForItsOwnerOnly() throws Exception {
        AGENT.enqueue(json(200, "{\"reports\":[" + VIEW + "]}"));
        mvc.perform(get("/v1/conversations/" + CONVERSATION + "/reports").header(HttpHeaders.AUTHORIZATION, bearer(token())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.reports[0].id").value(REPORT))
                .andExpect(jsonPath("$.data.reports[0].versions.length()").value(2));
        assertThat(AGENT.takeRequest(5, TimeUnit.SECONDS).getPath())
                .isEqualTo("/v1/conversations/" + CONVERSATION + "/reports");
        AuditEvent listed = singleAudit();
        assertThat(listed.action()).isEqualTo("report.list");
        assertThat(listed.detail()).containsEntry("reports", 1);

        auditLog().clear();
        mvc.perform(get("/v1/conversations/" + CONVERSATION + "/reports").header(HttpHeaders.AUTHORIZATION, bearer(otherUser())))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error.code").value("conversation_forbidden"));
        assertThat(singleAudit().status()).isEqualTo(AuditStatus.DENIED);
        mvc.perform(get("/v1/conversations/0b4c9a52-4a7e-4a8e-9f55-1d6d0c7b2e11/reports")
                        .header(HttpHeaders.AUTHORIZATION, bearer(token())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.reports").isEmpty());
        assertThat(AGENT.takeRequest(200, TimeUnit.MILLISECONDS)).isNull();
    }

    @Test
    void aVersionIsReadWithItsPageAndCitationsAndAStoreOutageIs503() throws Exception {
        AGENT.enqueue(json(200, versionJson()));
        mvc.perform(get("/v1/reports/" + REPORT + "/versions/1").header(HttpHeaders.AUTHORIZATION, bearer(token())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.html").value(PAGE))
                .andExpect(jsonPath("$.data.citations[0].kb_name.en").value("IMF"))
                .andExpect(jsonPath("$.data.citations[0].pages[0]").value(3))
                .andExpect(jsonPath("$.data.citations[0].published_on").value("2026-07-01"));
        assertThat(AGENT.takeRequest(5, TimeUnit.SECONDS).getPath()).isEqualTo("/v1/reports/" + REPORT + "/versions/1");
        AuditEvent event = singleAudit();
        assertThat(event.action()).isEqualTo("report.read");
        assertThat(event.detail()).containsEntry("version", 1);
        assertThat(event.detail().toString()).doesNotContain("التضخم");

        AGENT.enqueue(json(503, "{\"request_id\":\"x\",\"error\":{\"code\":\"report_unavailable\",\"message_ar\":\"م\","
                + "\"message_en\":\"m\",\"retryable\":true}}"));
        mvc.perform(get("/v1/reports/" + REPORT + "/versions/1").header(HttpHeaders.AUTHORIZATION, bearer(token())))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.error.code").value("report_unavailable"));

        for (String bad : List.of("/v1/reports/not-a-uuid", "/v1/reports/" + REPORT + "/versions/0",
                "/v1/reports/" + REPORT + "/versions/x", "/v1/reports/" + REPORT + "/versions/100001")) {
            mvc.perform(get(bad).header(HttpHeaders.AUTHORIZATION, bearer(token())))
                    .andExpect(status().isBadRequest())
                    .andExpect(jsonPath("$.error.code").value("invalid_request"));
        }
        mvc.perform(get("/v1/reports/" + REPORT)).andExpect(status().isUnauthorized());
    }

    private String viewLink(int version) throws Exception {
        AGENT.enqueue(json(200, VIEW));
        MockHttpServletResponse issued = mvc.perform(post("/v1/reports/" + REPORT + "/versions/" + version + "/view")
                        .header(HttpHeaders.AUTHORIZATION, bearer(token())))
                .andExpect(status().isOk())
                .andReturn().getResponse();
        AGENT.takeRequest(5, TimeUnit.SECONDS);
        JsonNode link = mapper.readTree(issued.getContentAsString()).path("data");
        assertThat(Instant.parse(link.path("expires_at").asText()))
                .isBetween(NOW.get().plusSeconds(58), NOW.get().plusSeconds(60));
        String url = link.path("url").asText();
        assertThat(url).startsWith("http://localhost:8080/report-views/");
        return url.substring("http://localhost:8080".length());
    }

    @Test
    void theViewLinkServesThePageUnderTheReportPolicyWithoutFrameOptionsOrCors() throws Exception {
        String path = viewLink(2);
        AuditEvent issued = singleAudit();
        assertThat(issued.action()).isEqualTo("report.view_link");
        assertThat(issued.detail()).containsEntry("version", 2);
        auditLog().clear();

        AGENT.enqueue(new MockResponse().setHeader("Content-Type", "text/html; charset=utf-8").setBody(PAGE));
        MockHttpServletResponse page = mvc.perform(get(path).header(HttpHeaders.ORIGIN, "http://localhost:3000"))
                .andExpect(status().isOk())
                .andReturn().getResponse();

        assertThat(page.getContentType()).isEqualTo("text/html; charset=utf-8");
        assertThat(page.getContentAsString(StandardCharsets.UTF_8)).isEqualTo(PAGE);
        assertThat(page.getHeaders("Content-Security-Policy")).containsExactly(CSP);
        assertThat(page.getHeader("X-Content-Type-Options")).isEqualTo("nosniff");
        assertThat(page.getHeader("Referrer-Policy")).isEqualTo("no-referrer");
        assertThat(page.getHeaders(HttpHeaders.CACHE_CONTROL)).containsExactly("no-store");
        assertThat(page.getHeader("X-Frame-Options")).isNull();
        assertThat(page.getHeaderNames()).noneMatch(h -> h.toLowerCase().startsWith("access-control-"));

        RecordedRequest upstream = AGENT.takeRequest(5, TimeUnit.SECONDS);
        assertThat(upstream.getPath()).isEqualTo(
                "/v1/reports/" + REPORT + "/versions/2/page?form=view&kit_base=" + KIT);
        assertThat(upstream.getHeader("X-User-Id")).isEqualTo("user-123");
        assertThat(upstream.getHeader("X-Tenant-Id")).isEqualTo("tenant-a");
        AuditEvent viewed = singleAudit();
        assertThat(viewed.action()).isEqualTo("report.view");
        assertThat(viewed.userId()).isEqualTo("user-123");
        assertThat(viewed.detail()).containsEntry("report_id", REPORT).containsEntry("version", 2);

        // The API keeps its frame protection.
        AGENT.enqueue(json(200, VIEW));
        assertThat(mvc.perform(get("/v1/reports/" + REPORT).header(HttpHeaders.AUTHORIZATION, bearer(token())))
                .andReturn().getResponse().getHeader("X-Frame-Options")).isEqualTo("DENY");
    }

    @Test
    void aVersionPastTheLatestGetsNoLink() throws Exception {
        AGENT.enqueue(json(200, VIEW));
        mvc.perform(post("/v1/reports/" + REPORT + "/versions/3/view").header(HttpHeaders.AUTHORIZATION, bearer(token())))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error.code").value("report_not_found"));
    }

    @Test
    void aTamperedOrExpiredTicketIs404WithoutABodyAndNeverReachesTheAgent() throws Exception {
        String path = viewLink(1);
        String ticket = path.substring("/report-views/".length());
        String payload = ticket.substring(0, ticket.indexOf('.'));
        String mac = ticket.substring(ticket.indexOf('.') + 1);
        // The same payload naming another version, signed by nobody; a flipped MAC; garbage; no MAC.
        String otherVersion = java.util.Base64.getUrlEncoder().withoutPadding().encodeToString(
                new String(java.util.Base64.getUrlDecoder().decode(payload), StandardCharsets.UTF_8)
                        .replace(",1,", ",2,").getBytes(StandardCharsets.UTF_8));
        char flipped = mac.charAt(0) == 'A' ? 'B' : 'A';
        for (String bad : List.of(otherVersion + "." + mac, payload + "." + flipped + mac.substring(1),
                "not-a-ticket", payload, payload + ".", "." + mac, "x".repeat(2000))) {
            MockHttpServletResponse refused = mvc.perform(get("/report-views/" + bad))
                    .andExpect(status().isNotFound()).andReturn().getResponse();
            assertThat(refused.getContentLength()).isZero();
        }
        // Expired: valid until 60 s after it was issued.
        NOW.set(NOW.get().plus(Duration.ofSeconds(61)));
        mvc.perform(get(path)).andExpect(status().isNotFound());
        assertThat(AGENT.takeRequest(200, TimeUnit.MILLISECONDS)).isNull();
    }

    @Test
    void theDownloadIsAnAttachmentUnderSandboxAndAudited() throws Exception {
        AGENT.enqueue(json(200, VIEW));
        AGENT.enqueue(new MockResponse().setHeader("Content-Type", "text/html; charset=utf-8").setBody(PAGE));

        MockHttpServletResponse file = mvc.perform(get("/v1/reports/" + REPORT + "/versions/2/download")
                        .header(HttpHeaders.AUTHORIZATION, bearer(token())))
                .andExpect(status().isOk())
                .andReturn().getResponse();

        assertThat(file.getHeader(HttpHeaders.CONTENT_DISPOSITION))
                .isEqualTo("attachment; filename=\"report-5d0c6a3e-v2.html\"");
        assertThat(file.getContentType()).isEqualTo("text/html; charset=utf-8");
        assertThat(file.getHeaders("Content-Security-Policy")).containsExactly("sandbox");
        assertThat(file.getHeader("X-Content-Type-Options")).isEqualTo("nosniff");
        assertThat(file.getHeaders(HttpHeaders.CACHE_CONTROL)).containsExactly("no-store");
        assertThat(file.getContentAsString(StandardCharsets.UTF_8)).isEqualTo(PAGE);
        AGENT.takeRequest(5, TimeUnit.SECONDS);
        assertThat(AGENT.takeRequest(5, TimeUnit.SECONDS).getPath())
                .isEqualTo("/v1/reports/" + REPORT + "/versions/2/page?form=download");
        AuditEvent event = singleAudit();
        assertThat(event.action()).isEqualTo("report.download");
        assertThat(event.conversationId()).isEqualTo(CONVERSATION);
        assertThat(event.detail()).containsEntry("version", 2);
    }

    private static Path kitDir() {
        return Path.of(System.getenv().getOrDefault("GATEWAY_REPORTS_KIT_DIR", "../../config/artifacts/kit"));
    }

    @Test
    void theKitServesItsListedFilesOnlyWithTheirHeaders() throws Exception {
        for (String[] file : List.of(new String[] {"kit.css", "text/css; charset=utf-8"},
                new String[] {"kit.js", "text/javascript; charset=utf-8"},
                new String[] {"vendor/echarts.min.js", "text/javascript; charset=utf-8"},
                new String[] {"fonts/ibm-plex-sans-arabic-arabic-400-normal.woff2", "font/woff2"},
                new String[] {"fonts/ibm-plex-sans-arabic-latin-700-normal.woff2", "font/woff2"})) {
            // A font load from the sandboxed page is a CORS request from origin "null".
            MockHttpServletResponse served = mvc.perform(get("/report-kit/v1/" + file[0]).header(HttpHeaders.ORIGIN, "null"))
                    .andExpect(status().isOk())
                    .andReturn().getResponse();
            assertThat(served.getContentType()).isEqualTo(file[1]);
            assertThat(served.getContentAsByteArray()).isEqualTo(Files.readAllBytes(kitDir().resolve(file[0])));
            assertThat(served.getHeader("Access-Control-Allow-Origin")).isEqualTo("*");
            assertThat(served.getHeader("Cross-Origin-Resource-Policy")).isEqualTo("cross-origin");
            assertThat(served.getHeader("X-Content-Type-Options")).isEqualTo("nosniff");
            assertThat(served.getHeaders(HttpHeaders.CACHE_CONTROL)).containsExactly("public, max-age=86400");
        }
        for (String unlisted : List.of("/report-kit/v1/SOURCE.md", "/report-kit/v1/fonts/OFL.txt",
                "/report-kit/v1/vendor/ECHARTS-LICENSE.txt", "/report-kit/v1/", "/report-kit/v1/fonts/",
                "/report-kit/v1/fonts", "/report-kit/v2/kit.css", "/report-kit/kit.css", "/report-kit/v1/KIT.CSS",
                "/report-kit/v1/kit.css/")) {
            assertThat(mvc.perform(get(unlisted)).andReturn().getResponse().getStatus()).as(unlisted).isEqualTo(404);
        }
        // A dot segment or an encoded name never reaches the kit: the firewall refuses it first.
        for (String path : List.of("/report-kit/v1/../report.yaml", "/report-kit/v1/kit%2Ecss")) {
            assertThat(mvc.perform(get(path)).andReturn().getResponse().getStatus()).as(path).isIn(400, 404);
        }
        assertThat(mvc.perform(post("/report-kit/v1/kit.css")).andReturn().getResponse().getStatus()).isIn(403, 405);
    }

    @Test
    void aTicketIsValidForSixtySecondsAndForItsOwnVersionOnly() {
        ViewTickets own = controlledTickets();
        var caller = new sa.ncai.agent.gateway.auth.GatewayPrincipal("user-123", "tenant-a", List.of(), null, false,
                List.of(), null);
        ViewTickets.Issued issued = own.issue(caller, REPORT, 3);
        ViewTickets.Ticket ticket = own.verify(issued.ticket()).orElseThrow();
        assertThat(ticket.userId()).isEqualTo("user-123");
        assertThat(ticket.tenantId()).isEqualTo("tenant-a");
        assertThat(ticket.reportId()).isEqualTo(REPORT);
        assertThat(ticket.version()).isEqualTo(3);
        // Another gateway's key (a restart) does not verify it.
        assertThat(controlledTickets().verify(issued.ticket())).isEmpty();
        NOW.set(issued.expiresAt());
        assertThat(own.verify(issued.ticket())).isEmpty();
    }
}
