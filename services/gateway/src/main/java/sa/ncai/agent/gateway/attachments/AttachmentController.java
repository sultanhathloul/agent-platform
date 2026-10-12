package sa.ncai.agent.gateway.attachments;

import com.fasterxml.jackson.databind.ObjectMapper;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.Parameter;
import io.swagger.v3.oas.annotations.media.Content;
import io.swagger.v3.oas.annotations.media.Schema;
import io.swagger.v3.oas.annotations.responses.ApiResponse;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.regex.Pattern;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestPart;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;
import sa.ncai.agent.gateway.api.ApiEnvelope;
import sa.ncai.agent.gateway.api.ApiErrorEnvelope;
import sa.ncai.agent.gateway.api.ErrorCode;
import sa.ncai.agent.gateway.api.ErrorResponses;
import sa.ncai.agent.gateway.api.RequestIdFilter;
import sa.ncai.agent.gateway.audit.AuditEvent;
import sa.ncai.agent.gateway.audit.AuditStatus;
import sa.ncai.agent.gateway.audit.AuditTrail;
import sa.ncai.agent.gateway.auth.GatewayPrincipal;
import sa.ncai.agent.gateway.chat.ConversationRepository;
import sa.ncai.agent.gateway.chat.ConversationRepository.ConversationOwner;
import sa.ncai.agent.gateway.proxy.AgentClient;
import sa.ncai.agent.gateway.proxy.AgentReply;
import sa.ncai.agent.gateway.proxy.AgentTurnRequest;
import sa.ncai.agent.gateway.proxy.AgentUnavailableException;

/**
 * A conversation's attachments (phase 04, A2; ADR-0126, ADR-0129). The browser uploads through the
 * gateway: the limits of config/docs.yaml are checked before anything is stored, the bytes go to
 * MinIO, and the agent registers and ingests them. Upload, list, status and download are for the
 * conversation's owner only; there is no delete, the product deletes no file (owner decision S4): another user's conversation is 403
 * {@code conversation_forbidden}, an attachment id the caller does not have in it (another user's
 * included) is 404 {@code attachment_not_found} (G6). Every request is audited fail-closed
 * (ADR-0042): refused while the log is unreachable, answered only once its record is written.
 * Requests rejected by validation are not audited (ADR-0013).
 */
@RestController
@Tag(name = "attachments")
public class AttachmentController {

    static final String UPLOAD = "attachment.upload";
    static final String LIST = "attachment.list";
    static final String STATUS = "attachment.status";
    static final String DOWNLOAD = "attachment.download";
    static final String DOCS_PERMISSION = "cap:docs";

    private static final Logger log = LoggerFactory.getLogger(AttachmentController.class);
    private static final Pattern UUID_PATTERN =
            Pattern.compile("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$");
    private static final int MAX_NAME = 255;

    private final AgentClient agent;
    private final ObjectStorage storage;
    private final DocsLimits limits;
    private final ConversationRepository conversations;
    private final AuditTrail audit;
    private final ErrorResponses errors;
    private final ObjectMapper mapper;

    public AttachmentController(
            AgentClient agent,
            ObjectStorage storage,
            DocsLimits limits,
            ConversationRepository conversations,
            AuditTrail audit,
            ErrorResponses errors,
            ObjectMapper mapper) {
        this.agent = agent;
        this.storage = storage;
        this.limits = limits;
        this.conversations = conversations;
        this.audit = audit;
        this.errors = errors;
        this.mapper = mapper;
    }

    /** The upload limits, so the composer can show them before a file is chosen (D2). */
    public record AttachmentLimits(
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) long maxFileBytes,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) int maxFilesPerConversation,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) int maxPdfPages,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<String> extensions) {}

    @GetMapping(path = "/v1/attachments/limits", produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(summary = "The upload limits of config/docs.yaml")
    public ResponseEntity<ApiEnvelope<AttachmentLimits>> limits(HttpServletRequest request) {
        AttachmentLimits body = new AttachmentLimits(limits.maxFileBytes(), limits.maxFilesPerConversation(),
                limits.maxPdfPages(), limits.kinds().keySet().stream().sorted().toList());
        return ResponseEntity.ok(ApiEnvelope.ok(RequestIdFilter.requestId(request), body));
    }

    @PostMapping(
            path = "/v1/conversations/{conversationId}/attachments",
            consumes = MediaType.MULTIPART_FORM_DATA_VALUE,
            produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(
            summary = "Uploads one file to a conversation",
            description = "Checked against the limits of config/docs.yaml before it is stored; the agent then "
                    + "indexes it in the background. Poll the attachment for its status.",
            responses = {
                @ApiResponse(responseCode = "201", description = "Stored and registered; status pending"),
                @ApiResponse(responseCode = "400", description = "invalid_request", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "403", description = "forbidden (no cap:docs) or conversation_forbidden",
                        content = @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "409", description = "attachment_limit", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "413", description = "file_too_large or too_many_pages",
                        content = @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "415", description = "file_type_unsupported", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "502", description = "agent_unavailable or storage_unavailable",
                        content = @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "503", description = "audit_unavailable", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class)))
            })
    public ResponseEntity<ApiEnvelope<AttachmentView>> upload(
            @PathVariable("conversationId") String conversationId,
            @RequestPart("file") MultipartFile file,
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        long started = System.nanoTime();
        GatewayPrincipal caller = GatewayPrincipal.from(jwt);
        String name = displayName(file.getOriginalFilename());
        if (!UUID_PATTERN.matcher(conversationId).matches() || name == null || file.isEmpty()) {
            return failed(request, response, ErrorCode.INVALID_REQUEST);
        }
        String conversation = conversationId.toLowerCase(Locale.ROOT);
        if (!audit.reachable()) {
            return failed(request, response, ErrorCode.AUDIT_UNAVAILABLE);
        }
        Map<String, Object> detail = new LinkedHashMap<>();
        detail.put("size", file.getSize());
        Call call = new Call(request, response, caller, UPLOAD, conversation, detail, started);
        if (!caller.permissions().contains(DOCS_PERMISSION)) {
            return call.refuse(ErrorCode.FORBIDDEN, AuditStatus.DENIED);
        }
        if (!conversations.bindOrGetOwner(conversation, caller.userId(), caller.tenantId())
                .is(caller.userId(), caller.tenantId())) {
            return call.refuse(ErrorCode.CONVERSATION_FORBIDDEN, AuditStatus.DENIED);
        }
        Optional<String> kind = limits.kindOf(name);
        if (kind.isEmpty()) {
            return call.refuse(ErrorCode.FILE_TYPE_UNSUPPORTED, AuditStatus.DENIED);
        }
        detail.put("kind", kind.get());
        if (file.getSize() > limits.maxFileBytes()) {
            return call.refuse(ErrorCode.FILE_TOO_LARGE, AuditStatus.DENIED);
        }
        Path temp = Files.createTempFile("upload-", ".bin");
        try {
            file.transferTo(temp);
            UploadInspector.Inspection inspection = UploadInspector.inspect(kind.get(), temp, limits.maxPdfPages());
            if (inspection.pages() != null) {
                detail.put("pages", inspection.pages());
            }
            if (inspection.refusal().isPresent()) {
                return call.refuse(inspection.refusal().get(), AuditStatus.DENIED);
            }
            String path = "/v1/conversations/" + conversation + "/attachments";
            AgentReply existing = agent.exchange(HttpMethod.GET, path, null, caller, call.requestId());
            if (existing.status() == 200) {
                AttachmentList list = mapper.readValue(existing.body(), AttachmentList.class);
                if (list.attachments().size() >= limits.maxFilesPerConversation()) {
                    return call.refuse(ErrorCode.ATTACHMENT_LIMIT, AuditStatus.DENIED);
                }
            }
            String key = conversation + "/" + UUID.randomUUID();
            String mime = mimeOf(kind.get());
            try {
                storage.put(key, temp, file.getSize(), mime);
            } catch (ObjectStorage.StorageUnavailableException e) {
                log.warn("{}", e.getMessage());
                return call.refuse(ErrorCode.STORAGE_UNAVAILABLE, AuditStatus.ERROR);
            }
            AttachmentRegistration registration = new AttachmentRegistration(
                    principal(caller), name, mime, file.getSize(), key);
            AgentReply reply;
            try {
                reply = agent.exchange(HttpMethod.POST, path, registration, null, call.requestId());
            } catch (AgentUnavailableException e) {
                discard(key);
                return call.refuse(ErrorCode.AGENT_UNAVAILABLE, AuditStatus.ERROR);
            }
            if (reply.status() != 201) {
                discard(key);
                ErrorCode code = refusal(reply.status());
                return call.refuse(code, code == ErrorCode.AGENT_UNAVAILABLE ? AuditStatus.ERROR : AuditStatus.DENIED);
            }
            AttachmentView view = mapper.readValue(reply.body(), AttachmentView.class);
            detail.put("attachment_id", view.id());
            if (!audit.append(call.event(AuditStatus.OK))) {
                return failed(request, response, ErrorCode.AUDIT_UNAVAILABLE);
            }
            return ResponseEntity.status(201).body(ApiEnvelope.ok(call.requestId(), view));
        } catch (AgentUnavailableException e) {
            return call.refuse(ErrorCode.AGENT_UNAVAILABLE, AuditStatus.ERROR);
        } finally {
            Files.deleteIfExists(temp);
        }
    }

    @GetMapping(path = "/v1/conversations/{conversationId}/attachments", produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(
            summary = "The caller's attachments of a conversation, oldest first",
            responses = {
                @ApiResponse(responseCode = "200", description = "The attachments and their status"),
                @ApiResponse(responseCode = "403", description = "conversation_forbidden", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "502", description = "agent_unavailable", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "503", description = "audit_unavailable", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class)))
            })
    public ResponseEntity<ApiEnvelope<AttachmentList>> list(
            @PathVariable("conversationId") String conversationId,
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        Owned owned = owned(conversationId, null, LIST, jwt, request, response);
        if (owned == null) {
            return null;
        }
        Call call = owned.call();
        if (owned.unused()) {
            // Nobody has used this conversation yet: it holds nothing, and reading binds nobody.
            return call.ok(new AttachmentList(List.of()));
        }
        AgentReply reply = call.agent(HttpMethod.GET, owned.path());
        if (reply == null) {
            return null;
        }
        if (reply.status() != 200) {
            return call.refuse(refusal(reply.status()), status(refusal(reply.status())));
        }
        AttachmentList list = mapper.readValue(reply.body(), AttachmentList.class);
        call.detail().put("attachments", list.attachments().size());
        return call.ok(list);
    }

    @GetMapping(
            path = "/v1/conversations/{conversationId}/attachments/{attachmentId}",
            produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(
            summary = "One attachment's status: pending, indexed, or failed with a reason",
            responses = {
                @ApiResponse(responseCode = "200", description = "The attachment"),
                @ApiResponse(responseCode = "403", description = "conversation_forbidden", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "404", description = "attachment_not_found, also for another user's id",
                        content = @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "502", description = "agent_unavailable", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "503", description = "audit_unavailable", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class)))
            })
    public ResponseEntity<ApiEnvelope<AttachmentView>> status(
            @PathVariable("conversationId") String conversationId,
            @PathVariable("attachmentId") String attachmentId,
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        Owned owned = owned(conversationId, attachmentId, STATUS, jwt, request, response);
        if (owned == null) {
            return null;
        }
        Call call = owned.call();
        if (owned.unused()) {
            return call.refuse(ErrorCode.ATTACHMENT_NOT_FOUND, AuditStatus.DENIED);
        }
        AgentReply reply = call.agent(HttpMethod.GET, owned.path());
        if (reply == null) {
            return null;
        }
        if (reply.status() != 200) {
            return call.refuse(refusal(reply.status()), status(refusal(reply.status())));
        }
        AttachmentView view = mapper.readValue(reply.body(), AttachmentView.class);
        call.detail().put("status", view.status());
        return call.ok(view);
    }

    @GetMapping(path = "/v1/conversations/{conversationId}/attachments/{attachmentId}/content")
    @Operation(
            summary = "Downloads one attachment's file (owner only)",
            responses = {
                @ApiResponse(responseCode = "200", description = "The file's bytes",
                        content = @Content(mediaType = MediaType.APPLICATION_OCTET_STREAM_VALUE,
                                schema = @Schema(type = "string", format = "binary"))),
                @ApiResponse(responseCode = "403", description = "conversation_forbidden", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "404", description = "attachment_not_found, also for another user's id",
                        content = @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "502", description = "agent_unavailable or storage_unavailable",
                        content = @Content(mediaType = MediaType.APPLICATION_JSON_VALUE,
                                schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "503", description = "audit_unavailable", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class)))
            })
    public void download(
            @PathVariable("conversationId") String conversationId,
            @PathVariable("attachmentId") String attachmentId,
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        Owned owned = owned(conversationId, attachmentId, DOWNLOAD, jwt, request, response);
        if (owned == null) {
            return;
        }
        Call call = owned.call();
        if (owned.unused()) {
            call.refuse(ErrorCode.ATTACHMENT_NOT_FOUND, AuditStatus.DENIED);
            return;
        }
        AgentReply reply = call.agent(HttpMethod.GET, owned.path());
        if (reply == null) {
            return;
        }
        if (reply.status() != 200) {
            call.refuse(refusal(reply.status()), status(refusal(reply.status())));
            return;
        }
        AgentAttachment attachment = mapper.readValue(reply.body(), AgentAttachment.class);
        InputStream bytes;
        try {
            bytes = storage.get(attachment.objectKey());
        } catch (ObjectStorage.StorageUnavailableException e) {
            log.warn("{}", e.getMessage());
            call.refuse(ErrorCode.STORAGE_UNAVAILABLE, AuditStatus.ERROR);
            return;
        }
        try (InputStream in = bytes) {
            if (!audit.append(call.event(AuditStatus.OK))) {
                errors.write(request, response, ErrorCode.AUDIT_UNAVAILABLE);
                return;
            }
            response.setStatus(200);
            response.setContentType(attachment.mime());
            response.setHeader(HttpHeaders.CONTENT_DISPOSITION,
                    "attachment; filename*=UTF-8''" + URLEncoder.encode(attachment.name(), StandardCharsets.UTF_8)
                            .replace("+", "%20"));
            response.setHeader("X-Content-Type-Options", "nosniff");
            OutputStream out = response.getOutputStream();
            in.transferTo(out);
            out.flush();
        }
    }

    // ------------------------------------------------------------------ shared steps

    /** A request on an existing conversation's attachments, after validation, audit check and ownership. */
    private record Owned(Call call, String path, boolean unused) {}

    /**
     * Validates the ids, checks the audit log, and refuses another owner's conversation with 403.
     * Returns null when the response is already written.
     */
    private Owned owned(
            String conversationId,
            String attachmentId,
            String action,
            Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        long started = System.nanoTime();
        GatewayPrincipal caller = GatewayPrincipal.from(jwt);
        if (!UUID_PATTERN.matcher(conversationId).matches()
                || (attachmentId != null && !UUID_PATTERN.matcher(attachmentId).matches())) {
            failed(request, response, ErrorCode.INVALID_REQUEST);
            return null;
        }
        String conversation = conversationId.toLowerCase(Locale.ROOT);
        String attachment = attachmentId == null ? null : attachmentId.toLowerCase(Locale.ROOT);
        if (!audit.reachable()) {
            failed(request, response, ErrorCode.AUDIT_UNAVAILABLE);
            return null;
        }
        Map<String, Object> detail = new LinkedHashMap<>();
        if (attachment != null) {
            detail.put("attachment_id", attachment);
        }
        Call call = new Call(request, response, caller, action, conversation, detail, started);
        Optional<ConversationOwner> owner = conversations.ownerOf(conversation);
        if (owner.isPresent() && !owner.get().is(caller.userId(), caller.tenantId())) {
            call.refuse(ErrorCode.CONVERSATION_FORBIDDEN, AuditStatus.DENIED);
            return null;
        }
        String path = "/v1/conversations/" + conversation + "/attachments" + (attachment == null ? "" : "/" + attachment);
        return new Owned(call, path, owner.isEmpty());
    }

    /** One audited request: its refusal, its success, and the agent call between. */
    private final class Call {
        private final HttpServletRequest request;
        private final HttpServletResponse response;
        private final GatewayPrincipal caller;
        private final String action;
        private final String conversationId;
        private final Map<String, Object> detail;
        private final long started;

        Call(HttpServletRequest request, HttpServletResponse response, GatewayPrincipal caller, String action,
                String conversationId, Map<String, Object> detail, long started) {
            this.request = request;
            this.response = response;
            this.caller = caller;
            this.action = action;
            this.conversationId = conversationId;
            this.detail = detail;
            this.started = started;
        }

        String requestId() {
            return RequestIdFilter.requestId(request);
        }

        Map<String, Object> detail() {
            return detail;
        }

        AuditEvent event(AuditStatus status) {
            Map<String, Object> recorded = new LinkedHashMap<>(detail);
            recorded.put("latency_ms", (System.nanoTime() - started) / 1_000_000);
            return new AuditEvent(requestId(), caller.userId(), caller.tenantId(), action, conversationId, status,
                    recorded);
        }

        /** Audits the refusal, then answers with {@code code}, or with audit_unavailable when the record failed. */
        <T> ResponseEntity<T> refuse(ErrorCode code, AuditStatus status) throws IOException {
            detail.put("error_code", code.code());
            return failed(request, response, audit.append(event(status)) ? code : ErrorCode.AUDIT_UNAVAILABLE);
        }

        <T> ResponseEntity<ApiEnvelope<T>> ok(T body) throws IOException {
            if (!audit.append(event(AuditStatus.OK))) {
                return failed(request, response, ErrorCode.AUDIT_UNAVAILABLE);
            }
            return ResponseEntity.ok(ApiEnvelope.ok(requestId(), body));
        }

        /** The agent's reply, or null when it was unreachable and the refusal is written. */
        AgentReply agent(HttpMethod method, String path) throws IOException {
            try {
                return AttachmentController.this.agent.exchange(method, path, null, caller, requestId());
            } catch (AgentUnavailableException e) {
                refuse(ErrorCode.AGENT_UNAVAILABLE, AuditStatus.ERROR);
                return null;
            }
        }
    }

    private static ErrorCode refusal(int status) {
        return switch (status) {
            case 403 -> ErrorCode.CONVERSATION_FORBIDDEN;
            case 404 -> ErrorCode.ATTACHMENT_NOT_FOUND;
            case 409 -> ErrorCode.ATTACHMENT_LIMIT;
            case 415 -> ErrorCode.FILE_TYPE_UNSUPPORTED;
            default -> ErrorCode.AGENT_UNAVAILABLE;
        };
    }

    private static AuditStatus status(ErrorCode code) {
        return code == ErrorCode.AGENT_UNAVAILABLE ? AuditStatus.ERROR : AuditStatus.DENIED;
    }

    private static AgentTurnRequest.Principal principal(GatewayPrincipal caller) {
        return new AgentTurnRequest.Principal(caller.userId(), caller.tenantId(), caller.permissions(), null,
                caller.displayName(), null, caller.policyRoles());
    }

    /** The file name without any directory part, at most 255 characters; null when there is none. */
    static String displayName(String original) {
        if (original == null) {
            return null;
        }
        String name = original.replace('\\', '/');
        name = name.substring(name.lastIndexOf('/') + 1).strip();
        if (name.isEmpty() || name.chars().anyMatch(Character::isISOControl)) {
            return null;
        }
        return name.length() > MAX_NAME ? name.substring(name.length() - MAX_NAME) : name;
    }

    private static String mimeOf(String kind) {
        return switch (kind) {
            case "pdf" -> "application/pdf";
            case "docx" -> "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
            case "xlsx" -> "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
            case "md" -> "text/markdown";
            default -> "text/plain";
        };
    }

    private void discard(String key) {
        try {
            storage.delete(key);
        } catch (ObjectStorage.StorageUnavailableException e) {
            log.warn("could not delete the unregistered upload {}: {}", key, e.getMessage());
        }
    }

    private <T> ResponseEntity<T> failed(HttpServletRequest request, HttpServletResponse response, ErrorCode code)
            throws IOException {
        errors.write(request, response, code);
        return null;
    }
}
