package sa.ncai.agent.gateway.attachments;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.multipart;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.RecordedRequest;
import org.apache.pdfbox.pdmodel.PDDocument;
import org.apache.pdfbox.pdmodel.PDPage;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpHeaders;
import org.springframework.mock.web.MockMultipartFile;
import sa.ncai.agent.gateway.GatewayTestSupport;
import sa.ncai.agent.gateway.InMemoryAuditRepository;
import sa.ncai.agent.gateway.InMemoryObjectStorage;
import sa.ncai.agent.gateway.audit.AuditEvent;
import sa.ncai.agent.gateway.audit.AuditStatus;

/**
 * The attachment endpoints (phase 04, A2; ADR-0126, ADR-0129). Isolation first (G6): another user,
 * by the owner's conversation id, is 403; by the owner's attachment id in their own conversation,
 * the agent's 404 reaches them as {@code attachment_not_found}. Limits are checked before anything
 * is stored, every operation is audited fail-closed, and the object key never reaches a client.
 */
class AttachmentProxyTest extends GatewayTestSupport {

    private static final String CONVERSATION = "7d1f2c3b-4a5e-4f60-8172-93a4b5c6d7e8";
    private static final String ATTACHMENT = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d";
    private static final String BASE = "/v1/conversations/" + CONVERSATION + "/attachments";

    @Autowired
    ObjectMapper mapper;

    @Autowired
    UploadRateLimiter rateLimiter;

    @Autowired
    DocsLimits limits;

    @org.junit.jupiter.api.BeforeEach
    void freshAllowance() {
        rateLimiter.clear();
    }

    private static String viewer() {
        return token(claims -> claims.subject("viewer-1").claim("realm_access",
                Map.of("roles", List.of("user", "cap:general", "cap:docs"))));
    }

    private static String other() {
        return token(claims -> claims.subject("other-2").claim("realm_access",
                Map.of("roles", List.of("user", "cap:general", "cap:docs"))));
    }

    private static String view(String status, String reason) {
        return """
                {"id":"%s","conversation_id":"%s","name":"لائحة الإجازات.pdf","mime":"application/pdf",\
                "kind":"pdf","size":1234,"status":"%s","reason":%s,"pages":3,"chunks":null,\
                "created_at":"2026-10-11T10:00:00Z","indexed_at":null,\
                "object_key":"%s/secret-key"}""".formatted(ATTACHMENT, CONVERSATION, status,
                reason == null ? "null" : "\"" + reason + "\"", CONVERSATION);
    }

    private static MockResponse json(int status, String body) {
        return new MockResponse().setResponseCode(status).setHeader("Content-Type", "application/json").setBody(body);
    }

    private static byte[] pdf(int pages) throws IOException {
        try (PDDocument document = new PDDocument(); ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            for (int i = 0; i < pages; i++) {
                document.addPage(new PDPage());
            }
            document.save(out);
            return out.toByteArray();
        }
    }

    private static MockMultipartFile file(String name, byte[] bytes) {
        return new MockMultipartFile("file", name, "application/octet-stream", bytes);
    }

    private InMemoryObjectStorage objects() {
        return (InMemoryObjectStorage) storage;
    }

    private AuditEvent singleAudit() {
        List<AuditEvent> events = auditLog().events();
        assertThat(events).hasSize(1);
        return events.getFirst();
    }

    // ------------------------------------------------------------------ isolation (G6)

    @Test
    void anotherUsersConversationIsForbiddenForEveryOperationAndNothingReachesTheAgent() throws Exception {
        conversations.bindOrGetOwner(CONVERSATION, "viewer-1", "tenant-a");
        String intruder = bearer(other());

        mvc.perform(get(BASE).header(HttpHeaders.AUTHORIZATION, intruder))
                .andExpect(status().isForbidden()).andExpect(jsonPath("$.error.code").value("conversation_forbidden"));
        mvc.perform(get(BASE + "/" + ATTACHMENT).header(HttpHeaders.AUTHORIZATION, intruder))
                .andExpect(status().isForbidden());
        mvc.perform(get(BASE + "/" + ATTACHMENT + "/content").header(HttpHeaders.AUTHORIZATION, intruder))
                .andExpect(status().isForbidden());
        mvc.perform(multipart(BASE).file(file("ملف.pdf", pdf(1))).header(HttpHeaders.AUTHORIZATION, intruder))
                .andExpect(status().isForbidden());

        assertThat(AGENT.takeRequest(200, TimeUnit.MILLISECONDS)).isNull();
        assertThat(objects().objects).isEmpty();
        assertThat(auditLog().events()).hasSize(4)
                .allSatisfy(e -> assertThat(e.status()).isEqualTo(AuditStatus.DENIED))
                .extracting(AuditEvent::action)
                .containsExactly("attachment.list", "attachment.status", "attachment.download", "attachment.upload");
    }

    @Test
    void anotherUsersAttachmentIdInTheCallersOwnConversationIsNotFound() throws Exception {
        conversations.bindOrGetOwner(CONVERSATION, "other-2", "tenant-a");
        AGENT.enqueue(json(404, "{\"request_id\":\"x\",\"error\":{\"code\":\"attachment_not_found\","
                + "\"message_ar\":\"-\",\"message_en\":\"-\"}}"));
        mvc.perform(get(BASE + "/" + ATTACHMENT).header(HttpHeaders.AUTHORIZATION, bearer(other())))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error.code").value("attachment_not_found"))
                .andExpect(jsonPath("$.error.message_ar").value("لم يُعثر على هذا الملف في المحادثة."));
        RecordedRequest upstream = AGENT.takeRequest(5, TimeUnit.SECONDS);
        assertThat(upstream.getHeader("X-User-Id")).isEqualTo("other-2");
        assertThat(singleAudit().status()).isEqualTo(AuditStatus.DENIED);
    }

    /**
     * Beyond the agent's 404 (review round 1): the gateway itself never lets a caller choose whose
     * file it reads. The agent is asked as the caller (identity from the token, never a header the
     * caller sends), at the caller's own conversation path; and a download the agent refuses never
     * touches storage, even when the object exists under its key.
     */
    @Test
    void theAgentIsAskedAsTheCallerAndARefusedDownloadNeverReadsStorage() throws Exception {
        conversations.bindOrGetOwner(CONVERSATION, "other-2", "tenant-a");
        objects().objects.put(CONVERSATION + "/secret-key", "%PDF-1.7 the owner's".getBytes(StandardCharsets.US_ASCII));
        AGENT.enqueue(json(404, "{\"request_id\":\"x\",\"error\":{\"code\":\"attachment_not_found\","
                + "\"message_ar\":\"-\",\"message_en\":\"-\"}}"));
        mvc.perform(get(BASE + "/" + ATTACHMENT + "/content")
                        .header(HttpHeaders.AUTHORIZATION, bearer(other()))
                        .header("X-User-Id", "viewer-1")          // a caller's attempt to speak for another user
                        .header("X-Tenant-Id", "tenant-b"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error.code").value("attachment_not_found"));
        RecordedRequest upstream = AGENT.takeRequest(5, TimeUnit.SECONDS);
        assertThat(upstream.getPath()).isEqualTo(BASE + "/" + ATTACHMENT);
        assertThat(upstream.getHeader("X-User-Id")).isEqualTo("other-2");
        assertThat(upstream.getHeader("X-Tenant-Id")).isEqualTo("tenant-a");
        assertThat(objects().reads).isEmpty();
    }

    @Test
    void theListAndTheStatusNeverCarryTheObjectKeyToTheClient() throws Exception {
        conversations.bindOrGetOwner(CONVERSATION, "viewer-1", "tenant-a");
        AGENT.enqueue(json(200, "{\"attachments\":[" + view("indexed", null) + "]}"));
        AGENT.enqueue(json(200, view("indexed", null)));
        for (String path : List.of(BASE, BASE + "/" + ATTACHMENT)) {
            String body = mvc.perform(get(path).header(HttpHeaders.AUTHORIZATION, bearer(viewer())))
                    .andExpect(status().isOk())
                    .andReturn().getResponse().getContentAsString(StandardCharsets.UTF_8);
            assertThat(body).doesNotContain("secret-key").doesNotContain("object_key");
        }
    }

    @Test
    void aConversationNobodyUsedHoldsNothingAndBindsNobody() throws Exception {
        mvc.perform(get(BASE).header(HttpHeaders.AUTHORIZATION, bearer(viewer())))
                .andExpect(status().isOk()).andExpect(jsonPath("$.data.attachments").isEmpty());
        mvc.perform(get(BASE + "/" + ATTACHMENT).header(HttpHeaders.AUTHORIZATION, bearer(viewer())))
                .andExpect(status().isNotFound());
        assertThat(conversations.ownerOf(CONVERSATION)).isEmpty();
        assertThat(AGENT.takeRequest(200, TimeUnit.MILLISECONDS)).isNull();
    }

    // ------------------------------------------------------------------ upload

    @Test
    void anUploadIsStoredRegisteredWithTheCallerFromTheTokenAndAudited() throws Exception {
        AGENT.enqueue(json(200, "{\"attachments\":[]}"));
        AGENT.enqueue(json(201, view("pending", null)));
        byte[] bytes = pdf(3);

        String body = mvc.perform(multipart(BASE.toUpperCase().replace("/V1/CONVERSATIONS", "/v1/conversations")
                                .replace("/ATTACHMENTS", "/attachments"))
                        .file(file("C:\\Users\\x\\لائحة الإجازات.pdf", bytes))
                        .header(HttpHeaders.AUTHORIZATION, bearer(viewer())))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.data.status").value("pending"))
                .andExpect(jsonPath("$.data.object_key").doesNotExist())
                .andReturn().getResponse().getContentAsString(StandardCharsets.UTF_8);
        assertThat(body).doesNotContain("secret-key");

        RecordedRequest list = AGENT.takeRequest(5, TimeUnit.SECONDS);
        assertThat(list.getMethod()).isEqualTo("GET");
        RecordedRequest registered = AGENT.takeRequest(5, TimeUnit.SECONDS);
        assertThat(registered.getPath()).isEqualTo(BASE);
        JsonNode registration = mapper.readTree(registered.getBody().readUtf8());
        assertThat(registration.path("principal").path("user_id").asText()).isEqualTo("viewer-1");
        assertThat(registration.path("name").asText()).isEqualTo("لائحة الإجازات.pdf");
        assertThat(registration.path("mime").asText()).isEqualTo("application/pdf");
        String key = registration.path("object_key").asText();
        assertThat(key).startsWith(CONVERSATION + "/");
        assertThat(objects().objects.get(key)).isEqualTo(bytes);

        AuditEvent event = singleAudit();
        assertThat(event.action()).isEqualTo("attachment.upload");
        assertThat(event.status()).isEqualTo(AuditStatus.OK);
        assertThat(event.conversationId()).isEqualTo(CONVERSATION);
        assertThat(event.detail()).containsEntry("attachment_id", ATTACHMENT).containsEntry("pages", 3)
                .containsEntry("kind", "pdf").doesNotContainKey("name");
    }

    @Test
    void anUploadWithoutTheDocsPermissionIsForbidden() throws Exception {
        String analyst = token(claims -> claims.claim("realm_access", Map.of("roles", List.of("user", "cap:sql"))));
        mvc.perform(multipart(BASE).file(file("ملف.pdf", pdf(1))).header(HttpHeaders.AUTHORIZATION, bearer(analyst)))
                .andExpect(status().isForbidden()).andExpect(jsonPath("$.error.code").value("forbidden"));
        assertThat(objects().objects).isEmpty();
        assertThat(singleAudit().status()).isEqualTo(AuditStatus.DENIED);
    }

    @Test
    void listStatusAndDownloadNeedTheDocsPermissionToo() throws Exception {
        conversations.bindOrGetOwner(CONVERSATION, "sql-only", "tenant-a");
        String noDocs = bearer(token(claims -> claims.subject("sql-only")
                .claim("realm_access", Map.of("roles", List.of("user", "cap:sql")))));
        for (String path : List.of(BASE, BASE + "/" + ATTACHMENT, BASE + "/" + ATTACHMENT + "/content")) {
            mvc.perform(get(path).header(HttpHeaders.AUTHORIZATION, noDocs))
                    .andExpect(status().isForbidden()).andExpect(jsonPath("$.error.code").value("forbidden"));
        }
        assertThat(AGENT.takeRequest(200, TimeUnit.MILLISECONDS)).isNull(); // refused before the agent
        assertThat(auditLog().events()).extracting(AuditEvent::action)
                .containsExactly("attachment.list", "attachment.status", "attachment.download");
        assertThat(auditLog().events()).allSatisfy(e -> {
            assertThat(e.status()).isEqualTo(AuditStatus.DENIED);
            assertThat(e.detail()).containsEntry("error_code", "forbidden");
        });
    }

    @Test
    void uploadsPastTheUsersAllowanceAre429AndAudited() throws Exception {
        rateLimiter.clear();
        for (int i = 0; i < limits.uploadsPerMinute(); i++) {
            assertThat(rateLimiter.allow("tenant-a/viewer-1")).isTrue();
        }
        mvc.perform(multipart(BASE).file(file("ملف.pdf", pdf(1))).header(HttpHeaders.AUTHORIZATION, bearer(viewer())))
                .andExpect(status().isTooManyRequests())
                .andExpect(jsonPath("$.error.code").value("upload_rate_limited"))
                .andExpect(jsonPath("$.error.message_ar").value("رفعت ملفات كثيرة خلال دقيقة. انتظر قليلًا ثم أعد المحاولة."));
        assertThat(objects().objects).isEmpty();
        assertThat(singleAudit().status()).isEqualTo(AuditStatus.DENIED);
        // Another user is not held back by this one.
        assertThat(rateLimiter.allow("tenant-a/other-2")).isTrue();
        rateLimiter.clear();
    }

    @Test
    void limitsAreCheckedBeforeAnythingIsStoredWithArabicMessages() throws Exception {
        String viewer = bearer(viewer());
        byte[] oversize = new byte[20 * 1024 * 1024 + 1];
        System.arraycopy("%PDF-1.7".getBytes(StandardCharsets.US_ASCII), 0, oversize, 0, 8);
        mvc.perform(multipart(BASE).file(file("كبير.pdf", oversize)).header(HttpHeaders.AUTHORIZATION, viewer))
                .andExpect(status().isPayloadTooLarge())
                .andExpect(jsonPath("$.error.code").value("file_too_large"))
                .andExpect(jsonPath("$.error.message_ar").value("حجم الملف أكبر من الحد المسموح به."));
        mvc.perform(multipart(BASE).file(file("برنامج.exe", new byte[] {1, 2})).header(HttpHeaders.AUTHORIZATION, viewer))
                .andExpect(status().isUnsupportedMediaType())
                .andExpect(jsonPath("$.error.code").value("file_type_unsupported"));
        mvc.perform(multipart(BASE).file(file("مزيف.pdf", "نص عادي".getBytes(StandardCharsets.UTF_8)))
                        .header(HttpHeaders.AUTHORIZATION, viewer))
                .andExpect(status().isUnsupportedMediaType());
        mvc.perform(multipart(BASE).file(file("طويل.pdf", pdf(301))).header(HttpHeaders.AUTHORIZATION, viewer))
                .andExpect(status().isPayloadTooLarge())
                .andExpect(jsonPath("$.error.code").value("too_many_pages"));
        AGENT.enqueue(json(200, "{\"attachments\":[" + String.join(",", java.util.Collections.nCopies(10,
                view("indexed", null))) + "]}"));
        mvc.perform(multipart(BASE).file(file("الحادي عشر.pdf", pdf(1))).header(HttpHeaders.AUTHORIZATION, viewer))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.error.code").value("attachment_limit"));

        assertThat(objects().objects).isEmpty();
        assertThat(auditLog().events()).hasSize(5).allSatisfy(e -> {
            assertThat(e.action()).isEqualTo("attachment.upload");
            assertThat(e.status()).isEqualTo(AuditStatus.DENIED);
        });
    }

    @Test
    void anAgentRefusalAfterStoringDiscardsTheObject() throws Exception {
        AGENT.enqueue(json(200, "{\"attachments\":[]}"));
        AGENT.enqueue(json(409, "{}"));
        mvc.perform(multipart(BASE).file(file("ملف.md", "# عنوان".getBytes(StandardCharsets.UTF_8)))
                        .header(HttpHeaders.AUTHORIZATION, bearer(viewer())))
                .andExpect(status().isConflict()).andExpect(jsonPath("$.error.code").value("attachment_limit"));
        assertThat(objects().objects).isEmpty();
    }

    @Test
    void aStorageOutageIsStorageUnavailable() throws Exception {
        AGENT.enqueue(json(200, "{\"attachments\":[]}"));
        objects().failing = true;
        mvc.perform(multipart(BASE).file(file("ملف.txt", "نص".getBytes(StandardCharsets.UTF_8)))
                        .header(HttpHeaders.AUTHORIZATION, bearer(viewer())))
                .andExpect(status().isBadGateway()).andExpect(jsonPath("$.error.code").value("storage_unavailable"));
        assertThat(singleAudit().status()).isEqualTo(AuditStatus.ERROR);
    }

    @Test
    void anUnreachableAuditLogRefusesTheUploadBeforeAnythingIsStored() throws Exception {
        auditLog().setMode(InMemoryAuditRepository.Mode.DOWN);
        mvc.perform(multipart(BASE).file(file("ملف.pdf", pdf(1))).header(HttpHeaders.AUTHORIZATION, bearer(viewer())))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.error.code").value("audit_unavailable"));
        assertThat(objects().objects).isEmpty();
    }

    // ------------------------------------------------------------------ status and download

    @Test
    void statusAndDownloadServeTheOwnerAndAreAudited() throws Exception {
        conversations.bindOrGetOwner(CONVERSATION, "viewer-1", "tenant-a");
        String viewer = bearer(viewer());
        objects().objects.put(CONVERSATION + "/secret-key", "%PDF-1.7 bytes".getBytes(StandardCharsets.US_ASCII));

        AGENT.enqueue(json(200, view("failed", "scanned_unsupported")));
        mvc.perform(get(BASE + "/" + ATTACHMENT).header(HttpHeaders.AUTHORIZATION, viewer))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.status").value("failed"))
                .andExpect(jsonPath("$.data.reason").value("scanned_unsupported"))
                .andExpect(jsonPath("$.data.object_key").doesNotExist());

        AGENT.enqueue(json(200, view("indexed", null)));
        byte[] downloaded = mvc.perform(get(BASE + "/" + ATTACHMENT + "/content").header(HttpHeaders.AUTHORIZATION, viewer))
                .andExpect(status().isOk())
                .andExpect(header().string(HttpHeaders.CONTENT_TYPE, "application/pdf"))
                .andExpect(header().string(HttpHeaders.CONTENT_DISPOSITION,
                        "attachment; filename*=UTF-8''%D9%84%D8%A7%D8%A6%D8%AD%D8%A9%20%D8%A7%D9%84%D8%A5%D8%AC%D8%A7%D8%B2%D8%A7%D8%AA.pdf"))
                .andReturn().getResponse().getContentAsByteArray();
        assertThat(new String(downloaded, StandardCharsets.US_ASCII)).isEqualTo("%PDF-1.7 bytes");

        assertThat(auditLog().events()).extracting(AuditEvent::action)
                .containsExactly("attachment.status", "attachment.download");
        assertThat(auditLog().events()).allSatisfy(e -> assertThat(e.status()).isEqualTo(AuditStatus.OK));
    }

    @Test
    void thereIsNoDeleteTheProductDeletesNoFile() throws Exception {
        conversations.bindOrGetOwner(CONVERSATION, "viewer-1", "tenant-a");
        mvc.perform(delete(BASE + "/" + ATTACHMENT).header(HttpHeaders.AUTHORIZATION, bearer(viewer())))
                .andExpect(status().isMethodNotAllowed());
        assertThat(AGENT.takeRequest(200, TimeUnit.MILLISECONDS)).isNull();
    }

    @Test
    void theLimitsAreServedFromTheDocsConfig() throws Exception {
        mvc.perform(get("/v1/attachments/limits").header(HttpHeaders.AUTHORIZATION, bearer(viewer())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.max_file_bytes").value(20971520))
                .andExpect(jsonPath("$.data.max_files_per_conversation").value(10))
                .andExpect(jsonPath("$.data.max_pdf_pages").value(300))
                .andExpect(jsonPath("$.data.extensions[0]").value("docx"));
    }

    @Test
    void attachmentsRequireAuthentication() throws Exception {
        mvc.perform(get(BASE)).andExpect(status().isUnauthorized());
    }

    @Test
    void fileNamesLoseTheirDirectoryAndControlCharacters() {
        assertThat(AttachmentController.displayName("C:\\a\\b\\تقرير.pdf")).isEqualTo("تقرير.pdf");
        assertThat(AttachmentController.displayName("../../etc/passwd.txt")).isEqualTo("passwd.txt");
        assertThat(AttachmentController.displayName("bad\u0000name.txt")).isNull();
        assertThat(AttachmentController.displayName("   ")).isNull();
    }
}
