package sa.ncai.agent.gateway.kb;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.Parameter;
import io.swagger.v3.oas.annotations.media.Content;
import io.swagger.v3.oas.annotations.media.Schema;
import io.swagger.v3.oas.annotations.responses.ApiResponse;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import jakarta.validation.Valid;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.function.BiConsumer;
import java.util.regex.Pattern;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RequestPart;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;
import sa.ncai.agent.gateway.api.ApiEnvelope;
import sa.ncai.agent.gateway.api.ApiErrorEnvelope;
import sa.ncai.agent.gateway.api.ErrorCode;
import sa.ncai.agent.gateway.api.ErrorResponses;
import sa.ncai.agent.gateway.api.RequestIdFilter;
import sa.ncai.agent.gateway.attachments.AttachmentController;
import sa.ncai.agent.gateway.attachments.DocsLimits;
import sa.ncai.agent.gateway.attachments.ObjectStorage;
import sa.ncai.agent.gateway.attachments.UploadInspector;
import sa.ncai.agent.gateway.audit.AuditEvent;
import sa.ncai.agent.gateway.audit.AuditStatus;
import sa.ncai.agent.gateway.audit.AuditTrail;
import sa.ncai.agent.gateway.auth.GatewayPrincipal;
import sa.ncai.agent.gateway.auth.KeycloakAdminClient;
import sa.ncai.agent.gateway.authz.KbAccessPolicy;
import sa.ncai.agent.gateway.proxy.AgentClient;
import sa.ncai.agent.gateway.proxy.AgentReply;
import sa.ncai.agent.gateway.proxy.AgentUnavailableException;

/**
 * Knowledge base administration (phase 08, ADR-0333 to ADR-0339), admin only at the gateway (the
 * token's {@code admin} role) and at the agent ({@code X-User-Admin}, ADR-0284). Every request is
 * audited fail-closed (ADR-0042): refused while the log is unreachable, answered only once its record
 * is written, a non-admin's attempt recorded {@code denied}, with the actor's username and display
 * name (ADR-0123).
 *
 * <p>Creating a KB creates its realm role {@code kb:<slug>:read} through the Keycloak Admin API
 * first (I1), bounded ({@link KeycloakAdminClient}); an existing role counts as created, so a
 * retried create converges; the agent then records the KB. Deleting is soft at the agent: hidden,
 * role kept, its documents purged from the index. A document is uploaded through the gateway with
 * its D3 metadata, under the same limits, rate, concurrency, deadline and inspection as a
 * conversation's attachments ({@code AttachmentGate}, I2), stored in MinIO under
 * {@code kb/<slug>/}, and registered with the agent.
 */
@RestController
@Tag(name = "admin-knowledge-bases")
public class KbAdminController {

    static final String BASE = "/v1/admin/kbs";
    static final String UPLOAD = "kb.document.upload";
    private static final Logger log = LoggerFactory.getLogger(KbAdminController.class);
    private static final Pattern SLUG = Pattern.compile(KbDtos.SLUG);
    private static final Pattern UUID_PATTERN =
            Pattern.compile("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$");
    private static final Map<String, ErrorCode> AGENT_CODES = Map.of(
            "kb_not_found", ErrorCode.KB_NOT_FOUND,
            "kb_exists", ErrorCode.KB_EXISTS,
            "kb_document_not_found", ErrorCode.KB_DOCUMENT_NOT_FOUND,
            "kb_supersedes_invalid", ErrorCode.KB_SUPERSEDES_INVALID,
            "file_type_unsupported", ErrorCode.FILE_TYPE_UNSUPPORTED,
            "too_many_uploads", ErrorCode.TOO_MANY_UPLOADS,
            "invalid_request", ErrorCode.INVALID_REQUEST,
            "forbidden", ErrorCode.FORBIDDEN);

    private final AgentClient agent;
    private final KeycloakAdminClient keycloak;
    private final ObjectStorage storage;
    private final DocsLimits limits;
    private final AuditTrail audit;
    private final ErrorResponses errors;
    private final ObjectMapper mapper;

    public KbAdminController(AgentClient agent, KeycloakAdminClient keycloak, ObjectStorage storage, DocsLimits limits,
            AuditTrail audit, ErrorResponses errors, ObjectMapper mapper) {
        this.agent = agent;
        this.keycloak = keycloak;
        this.storage = storage;
        this.limits = limits;
        this.audit = audit;
        this.errors = errors;
        this.mapper = mapper;
    }

    // ------------------------------------------------------------------ knowledge bases

    @GetMapping(path = BASE, produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(summary = "The tenant's live knowledge bases, each with its role and document counts", responses = {
        @ApiResponse(responseCode = "200", description = "The knowledge bases"),
        @ApiResponse(responseCode = "403", description = "forbidden: not an admin", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "502", description = "agent_unavailable", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "503", description = "audit_unavailable", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class)))
    })
    public ResponseEntity<ApiEnvelope<KbDtos.KbList>> list(
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        Call call = admitted(jwt, request, response, "kb.list", null);
        if (call == null) {
            return null;
        }
        return call.proxy(HttpMethod.GET, BASE, null, KbDtos.KbList.class, 200,
                (detail, list) -> detail.put("kbs", list.kbs().size()));
    }

    @PostMapping(path = BASE, consumes = MediaType.APPLICATION_JSON_VALUE, produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(summary = "Creates a knowledge base and its realm role kb:<slug>:read", responses = {
        @ApiResponse(responseCode = "201", description = "Created, with its role"),
        @ApiResponse(responseCode = "400", description = "invalid_request", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "403", description = "forbidden: not an admin", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "409", description = "kb_exists: the slug is taken, by a live or a deleted KB",
                content = @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "502", description = "identity_unavailable (Keycloak) or agent_unavailable",
                content = @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "503", description = "audit_unavailable", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class)))
    })
    public ResponseEntity<ApiEnvelope<KbDtos.KbView>> create(
            @Valid @RequestBody KbDtos.KbCreate body,
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        Call call = admitted(jwt, request, response, "kb.create", body.slug());
        if (call == null) {
            return null;
        }
        String role = KbAccessPolicy.readPermission(body.slug());
        call.detail.put("role", role);
        try {
            // The role first (I1): a KB is never recorded without the role that reads it.
            call.detail.put("role_created", keycloak.ensureRealmRole(role, "Reads the knowledge base " + body.slug()));
        } catch (KeycloakAdminClient.IdentityUnavailableException e) {
            log.warn("{}", e.getMessage());
            return call.refuse(ErrorCode.IDENTITY_UNAVAILABLE, AuditStatus.ERROR);
        }
        Map<String, Object> agentBody = new LinkedHashMap<>();
        agentBody.put("slug", body.slug());
        agentBody.put("name", body.name());
        agentBody.put("description", body.description() == null ? new KbDtos.Description("", "") : body.description());
        if (body.icon() != null) {
            agentBody.put("icon", body.icon());
        }
        return call.proxy(HttpMethod.POST, BASE, agentBody, KbDtos.KbView.class, 201, (detail, kb) -> {});
    }

    @GetMapping(path = BASE + "/{slug}", produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(summary = "One knowledge base", responses = {
        @ApiResponse(responseCode = "200", description = "The knowledge base"),
        @ApiResponse(responseCode = "403", description = "forbidden: not an admin", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "404", description = "kb_not_found: another tenant's, deleted or unknown",
                content = @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class)))
    })
    public ResponseEntity<ApiEnvelope<KbDtos.KbView>> get(
            @PathVariable("slug") String slug,
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        Call call = admitted(jwt, request, response, "kb.read", slug);
        if (call == null) {
            return null;
        }
        return call.proxy(HttpMethod.GET, BASE + "/" + slug, null, KbDtos.KbView.class, 200, (detail, kb) -> {});
    }

    @DeleteMapping(path = BASE + "/{slug}", produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(summary = "Deletes a knowledge base: hidden, its role kept, its documents purged from the index",
            responses = {
        @ApiResponse(responseCode = "200", description = "Deleted"),
        @ApiResponse(responseCode = "403", description = "forbidden: not an admin", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "404", description = "kb_not_found", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class)))
    })
    public ResponseEntity<ApiEnvelope<KbDtos.KbDeleted>> delete(
            @PathVariable("slug") String slug,
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        Call call = admitted(jwt, request, response, "kb.delete", slug);
        if (call == null) {
            return null;
        }
        return call.proxy(HttpMethod.DELETE, BASE + "/" + slug, null, KbDtos.KbDeleted.class, 200,
                (detail, deleted) -> detail.put("index_purged", deleted.indexPurged()));
    }

    // ------------------------------------------------------------------ documents

    @GetMapping(path = BASE + "/{slug}/documents", produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(summary = "A knowledge base's documents with their status and D3 metadata, oldest first", responses = {
        @ApiResponse(responseCode = "200", description = "The documents"),
        @ApiResponse(responseCode = "403", description = "forbidden: not an admin", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "404", description = "kb_not_found", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class)))
    })
    public ResponseEntity<ApiEnvelope<KbDtos.KbDocumentList>> documents(
            @PathVariable("slug") String slug,
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        Call call = admitted(jwt, request, response, "kb.document.list", slug);
        if (call == null) {
            return null;
        }
        return call.proxy(HttpMethod.GET, BASE + "/" + slug + "/documents", null, KbDtos.KbDocumentList.class, 200,
                (detail, list) -> detail.put("documents", list.documents().size()));
    }

    @PostMapping(path = BASE + "/{slug}/documents", consumes = MediaType.MULTIPART_FORM_DATA_VALUE,
            produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(summary = "Uploads one document to a knowledge base with its source metadata",
            description = "Checked against the limits of config/docs.yaml before it is stored; the agent then indexes "
                    + "it in the background. Poll the document list for its status.",
            responses = {
        @ApiResponse(responseCode = "201", description = "Stored and registered; status pending"),
        @ApiResponse(responseCode = "400", description = "invalid_request", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "403", description = "forbidden: not an admin", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "404", description = "kb_not_found", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "408", description = "upload_timeout", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "409", description = "kb_supersedes_invalid", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "413", description = "file_too_large", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "415", description = "file_type_unsupported", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "429", description = "upload_rate_limited or too_many_uploads", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "502", description = "agent_unavailable or storage_unavailable", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "503", description = "audit_unavailable", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class)))
    })
    public ResponseEntity<ApiEnvelope<KbDtos.KbDocumentView>> upload(
            @PathVariable("slug") String slug,
            @RequestPart("file") MultipartFile file,
            @RequestParam("title") String title,
            @RequestParam("publisher") String publisher,
            @RequestParam("published_on") String publishedOn,
            @RequestParam(name = "source_url", required = false) String sourceUrl,
            @RequestParam(name = "supersedes", required = false) String supersedes,
            @Parameter(description = "The uploader has no source metadata for it: the admin page flags it (ADR-0392)")
            @RequestParam(name = "metadata_missing", required = false, defaultValue = "false") boolean metadataMissing,
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        String name = AttachmentController.displayName(file.getOriginalFilename());
        KbDtos.SourceMetadata metadata = metadata(title, publisher, publishedOn, sourceUrl, supersedes);
        if (!SLUG.matcher(slug).matches() || name == null || file.isEmpty() || metadata == null) {
            return failed(request, response, ErrorCode.INVALID_REQUEST);
        }
        Call call = admitted(jwt, request, response, UPLOAD, slug);
        if (call == null) {
            return null;
        }
        call.detail.put("size", file.getSize());
        Optional<String> kind = limits.kindOf(name);
        if (kind.isEmpty()) {
            return call.refuse(ErrorCode.FILE_TYPE_UNSUPPORTED, AuditStatus.DENIED);
        }
        call.detail.put("kind", kind.get());
        if (file.getSize() > limits.maxFileBytes()) {
            return call.refuse(ErrorCode.FILE_TOO_LARGE, AuditStatus.DENIED);
        }
        Path temp = Files.createTempFile("kb-upload-", ".bin");
        try {
            file.transferTo(temp);
            // Magic bytes and archive header only; content bounds are the ingest service's sandbox's.
            Optional<ErrorCode> refusal = UploadInspector.inspect(kind.get(), temp);
            if (refusal.isPresent()) {
                return call.refuse(refusal.get(), AuditStatus.DENIED);
            }
            String key = "kb/" + slug + "/" + UUID.randomUUID();
            String mime = AttachmentController.mimeOf(kind.get());
            try {
                storage.put(key, temp, file.getSize(), mime);
            } catch (ObjectStorage.StorageUnavailableException e) {
                log.warn("{}", e.getMessage());
                return call.refuse(ErrorCode.STORAGE_UNAVAILABLE, AuditStatus.ERROR);
            }
            KbDtos.Registration registration = new KbDtos.Registration(
                    name, mime, file.getSize(), key, metadata, sha256(temp), metadataMissing);
            ResponseEntity<ApiEnvelope<KbDtos.KbDocumentView>> answered = call.proxy(HttpMethod.POST,
                    BASE + "/" + slug + "/documents", registration, KbDtos.KbDocumentView.class, 201,
                    (detail, view) -> detail.put("document_id", view.id()));
            if (answered == null) {
                discard(key); // refused or failed: the agent holds no record naming it
            }
            return answered;
        } finally {
            Files.deleteIfExists(temp);
        }
    }

    @GetMapping(path = BASE + "/{slug}/documents/{documentId}", produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(summary = "One knowledge base document", responses = {
        @ApiResponse(responseCode = "200", description = "The document"),
        @ApiResponse(responseCode = "403", description = "forbidden: not an admin", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "404", description = "kb_not_found or kb_document_not_found", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class)))
    })
    public ResponseEntity<ApiEnvelope<KbDtos.KbDocumentView>> document(
            @PathVariable("slug") String slug,
            @PathVariable("documentId") String documentId,
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        if (!UUID_PATTERN.matcher(documentId.toLowerCase(Locale.ROOT)).matches()) {
            return failed(request, response, ErrorCode.INVALID_REQUEST);
        }
        Call call = admitted(jwt, request, response, "kb.document.read", slug);
        if (call == null) {
            return null;
        }
        call.detail.put("document_id", documentId.toLowerCase(Locale.ROOT));
        return call.proxy(HttpMethod.GET, BASE + "/" + slug + "/documents/" + documentId.toLowerCase(Locale.ROOT),
                null, KbDtos.KbDocumentView.class, 200, (detail, view) -> {});
    }

    @PutMapping(path = BASE + "/{slug}/documents/{documentId}/metadata", consumes = MediaType.APPLICATION_JSON_VALUE,
            produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(summary = "Corrects a knowledge base document's source metadata, its earlier version included",
            responses = {
        @ApiResponse(responseCode = "200", description = "The document"),
        @ApiResponse(responseCode = "400", description = "invalid_request", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "403", description = "forbidden: not an admin", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "404", description = "kb_not_found or kb_document_not_found", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class))),
        @ApiResponse(responseCode = "409", description = "kb_supersedes_invalid", content = @Content(
                mediaType = MediaType.APPLICATION_JSON_VALUE, schema = @Schema(implementation = ApiErrorEnvelope.class)))
    })
    public ResponseEntity<ApiEnvelope<KbDtos.KbDocumentView>> metadata(
            @PathVariable("slug") String slug,
            @PathVariable("documentId") String documentId,
            @Valid @RequestBody KbDtos.SourceMetadata body,
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        if (!UUID_PATTERN.matcher(documentId.toLowerCase(Locale.ROOT)).matches()) {
            return failed(request, response, ErrorCode.INVALID_REQUEST);
        }
        Call call = admitted(jwt, request, response, "kb.document.metadata", slug);
        if (call == null) {
            return null;
        }
        call.detail.put("document_id", documentId.toLowerCase(Locale.ROOT));
        call.detail.put("supersedes", body.supersedes());
        return call.proxy(HttpMethod.PUT,
                BASE + "/" + slug + "/documents/" + documentId.toLowerCase(Locale.ROOT) + "/metadata", body,
                KbDtos.KbDocumentView.class, 200, (detail, view) -> {});
    }

    // ------------------------------------------------------------------ shared steps

    /** The stored file's SHA-256, lower-case hex: the seed step skips a file a KB already holds (ADR-0392). */
    static String sha256(Path file) throws IOException {
        try (java.io.InputStream in = Files.newInputStream(file)) {
            java.security.MessageDigest digest = java.security.MessageDigest.getInstance("SHA-256");
            byte[] buffer = new byte[65536];
            for (int read; (read = in.read(buffer)) > 0; ) {
                digest.update(buffer, 0, read);
            }
            return java.util.HexFormat.of().formatHex(digest.digest());
        } catch (java.security.NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 is part of every Java platform", e);
        }
    }

    /** D3 metadata from the upload's form fields; null when one is malformed. */
    static KbDtos.SourceMetadata metadata(
            String title, String publisher, String publishedOn, String sourceUrl, String supersedes) {
        String t = title == null ? "" : title.strip();
        String p = publisher == null ? "" : publisher.strip();
        String url = sourceUrl == null || sourceUrl.isBlank() ? null : sourceUrl.strip();
        String earlier = supersedes == null || supersedes.isBlank() ? null : supersedes.strip().toLowerCase(Locale.ROOT);
        if (t.isEmpty() || t.length() > 300 || p.isEmpty() || p.length() > 200
                || publishedOn == null || !publishedOn.matches("^\\d{4}-\\d{2}-\\d{2}$")
                || (url != null && (url.length() > 2000 || !url.matches("^https?://\\S+$")))
                || (earlier != null && !UUID_PATTERN.matcher(earlier).matches())) {
            return null;
        }
        try {
            java.time.LocalDate.parse(publishedOn);
        } catch (java.time.format.DateTimeParseException e) {
            return null;
        }
        return new KbDtos.SourceMetadata(t, p, url, publishedOn, earlier);
    }

    /** The audit check, then the admin check; null when the refusal is already written. */
    private Call admitted(Jwt jwt, HttpServletRequest request, HttpServletResponse response, String action, String slug)
            throws IOException {
        Call call = new Call(request, response, GatewayPrincipal.from(jwt), action, System.nanoTime());
        if (slug != null) {
            call.detail.put("kb", slug);
            if (!SLUG.matcher(slug).matches()) {
                failed(request, response, ErrorCode.INVALID_REQUEST);
                return null;
            }
        }
        if (!audit.reachable()) {
            failed(request, response, ErrorCode.AUDIT_UNAVAILABLE);
            return null;
        }
        if (!call.caller.admin()) {
            call.refuse(ErrorCode.FORBIDDEN, AuditStatus.DENIED);
            return null;
        }
        return call;
    }

    private void discard(String key) {
        try {
            storage.delete(key);
        } catch (ObjectStorage.StorageUnavailableException e) {
            log.warn("could not delete the unregistered KB upload {}: {}", key, e.getMessage());
        }
    }

    private <T> ResponseEntity<T> failed(HttpServletRequest request, HttpServletResponse response, ErrorCode code)
            throws IOException {
        errors.write(request, response, code);
        return null;
    }

    /** One audited admin request: its refusal, its agent call and its record. */
    private final class Call {
        private final HttpServletRequest request;
        private final HttpServletResponse response;
        private final GatewayPrincipal caller;
        private final String action;
        private final long started;
        private final Map<String, Object> detail = new LinkedHashMap<>();

        Call(HttpServletRequest request, HttpServletResponse response, GatewayPrincipal caller, String action,
                long started) {
            this.request = request;
            this.response = response;
            this.caller = caller;
            this.action = action;
            this.started = started;
            // Who acted, by name as the token said at the time (ADR-0123), on every record.
            detail.put("actor_username", caller.username());
            detail.put("actor_name", caller.displayName());
        }

        AuditEvent event(AuditStatus status) {
            Map<String, Object> recorded = new LinkedHashMap<>(detail);
            recorded.put("latency_ms", (System.nanoTime() - started) / 1_000_000);
            return new AuditEvent(RequestIdFilter.requestId(request), caller.userId(), caller.tenantId(), action, null,
                    status, recorded);
        }

        <T> ResponseEntity<T> refuse(ErrorCode code, AuditStatus status) throws IOException {
            return refuse(code, status, null);
        }

        <T> ResponseEntity<T> refuse(ErrorCode code, AuditStatus status, List<String> details) throws IOException {
            detail.put("error_code", code.code());
            if (!audit.append(event(status))) {
                return failed(request, response, ErrorCode.AUDIT_UNAVAILABLE);
            }
            errors.write(request, response, code, code.status(), details);
            return null;
        }

        /** The agent call, then the record; the agent's refusal is relayed by its code. */
        <T> ResponseEntity<ApiEnvelope<T>> proxy(HttpMethod method, String uri, Object body, Class<T> type, int okStatus,
                BiConsumer<Map<String, Object>, T> describe) throws IOException {
            AgentReply reply;
            try {
                reply = agent.exchangeAdmin(method, uri, body, caller, RequestIdFilter.requestId(request));
            } catch (AgentUnavailableException e) {
                log.warn("{}", e.getMessage());
                return refuse(ErrorCode.AGENT_UNAVAILABLE, AuditStatus.ERROR);
            }
            if (!reply.ok()) {
                JsonNode error = readError(reply);
                ErrorCode code = AGENT_CODES.getOrDefault(error.path("code").asText(""), ErrorCode.AGENT_UNAVAILABLE);
                List<String> details = new ArrayList<>();
                error.path("details").forEach(item -> details.add(item.asText()));
                if (!details.isEmpty()) {
                    detail.put("details", details);
                }
                AuditStatus status = code == ErrorCode.AGENT_UNAVAILABLE ? AuditStatus.ERROR : AuditStatus.DENIED;
                return refuse(code, status, details.isEmpty() ? null : details);
            }
            T value = mapper.readValue(reply.body(), type);
            describe.accept(detail, value);
            if (!audit.append(event(AuditStatus.OK))) {
                return failed(request, response, ErrorCode.AUDIT_UNAVAILABLE);
            }
            return ResponseEntity.status(okStatus).body(ApiEnvelope.ok(RequestIdFilter.requestId(request), value));
        }

        private JsonNode readError(AgentReply reply) {
            try {
                return mapper.readTree(reply.body()).path("error");
            } catch (IOException e) {
                return mapper.missingNode();
            }
        }
    }
}
