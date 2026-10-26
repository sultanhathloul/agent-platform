package sa.ncai.agent.gateway.attachments;

import jakarta.servlet.FilterChain;
import jakarta.servlet.MultipartConfigElement;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.time.Clock;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.springframework.http.HttpHeaders;
import org.springframework.http.server.PathContainer;
import org.springframework.http.server.RequestPath;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;
import org.springframework.web.filter.OncePerRequestFilter;
import org.springframework.web.util.ServletRequestPathUtils;
import sa.ncai.agent.gateway.api.ErrorCode;
import sa.ncai.agent.gateway.api.ErrorResponses;
import sa.ncai.agent.gateway.api.RequestIdFilter;
import sa.ncai.agent.gateway.audit.AuditEvent;
import sa.ncai.agent.gateway.audit.AuditStatus;
import sa.ncai.agent.gateway.audit.AuditTrail;
import sa.ncai.agent.gateway.auth.GatewayPrincipal;

/**
 * The attachment endpoints' gate (owner decision R3), after authentication and before any request
 * body is read. Upload, list, status and download need {@code cap:docs} (403 {@code forbidden}); a
 * knowledge base document upload ({@code POST /v1/admin/kbs/<slug>/documents}, phase 08, I2) needs the
 * admin role and then passes the same checks as an upload, counted with the uploader's attachments.
 * For an upload, in this order (review round 2, finding 2): the per-user rate limit counts the
 * attempt and refuses 429 {@code upload_rate_limited} past the allowance; then the declared length
 * over the limits of config/docs.yaml is 413 {@code file_too_large}; then a per-user concurrent
 * cap, and behind it a gateway-wide one, refuse 429 {@code too_many_uploads}. An admitted upload's
 * body must arrive within the upload deadline, or it is refused 408 {@code upload_timeout}
 * ({@link DeadlineUploadRequest}). The gate decides which endpoint a request is on from the path
 * Spring MVC routes on — decoded, matrix parameters removed — never the raw request URI, so no
 * spelling of a path reaches a handler around the gate (the verification of round 2; the firewall
 * rejects encoded paths before this anyway, {@code PathFirewall}). On every refusal of the upload path — and on an
 * unauthenticated upload (401) — the gate sets {@code Connection: close}, so the server closes the
 * socket instead of draining a body it already refused (bounded anyway by
 * {@code server.tomcat.max-swallow-size}). The concurrency slot is held for the whole request and
 * released when it completes. Each refusal is audited fail-closed (ADR-0042), like the controller's.
 */
public class AttachmentGate extends OncePerRequestFilter {

    static final String DOCS_PERMISSION = "cap:docs";
    private static final Pattern PATH =
            Pattern.compile("^/v1/conversations/([^/]+)/attachments(?:/([^/]+)(/content)?)?/?$");
    /** A knowledge base document upload (phase 08, I2): the same limits, for an admin only. */
    private static final Pattern KB_UPLOAD_PATH = Pattern.compile("^/v1/admin/kbs/[^/]+/documents/?$");
    static final String KB_UPLOAD = "kb.document.upload";
    private static final Pattern UUID_PATTERN =
            Pattern.compile("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$");

    private final DocsLimits limits;
    private final MultipartConfigElement multipart;
    private final Clock clock;
    private final UploadRateLimiter rateLimiter;
    private final UploadConcurrency concurrency;
    private final AuditTrail audit;
    private final ErrorResponses errors;

    public AttachmentGate(DocsLimits limits, MultipartConfigElement multipart, Clock clock,
            UploadRateLimiter rateLimiter, UploadConcurrency concurrency, AuditTrail audit, ErrorResponses errors) {
        this.limits = limits;
        this.multipart = multipart;
        this.clock = clock;
        this.rateLimiter = rateLimiter;
        this.concurrency = concurrency;
        this.audit = audit;
        this.errors = errors;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        String path = lookupPath(request);
        return !PATH.matcher(path).matches() && !isKbUpload(request, path);
    }

    private static boolean isKbUpload(HttpServletRequest request, String path) {
        return "POST".equals(request.getMethod()) && KB_UPLOAD_PATH.matcher(path).matches();
    }

    /**
     * The path Spring MVC matches handlers against: the path within the application, each segment
     * decoded and stripped of matrix parameters ({@code PathContainer.PathSegment#valueToMatch}).
     */
    static String lookupPath(HttpServletRequest request) {
        RequestPath path = ServletRequestPathUtils.hasParsedRequestPath(request)
                ? ServletRequestPathUtils.getParsedRequestPath(request)
                : RequestPath.parse(request.getRequestURI(), request.getContextPath());
        StringBuilder decoded = new StringBuilder();
        for (PathContainer.Element element : path.pathWithinApplication().elements()) {
            decoded.append(element instanceof PathContainer.PathSegment segment
                    ? segment.valueToMatch() : element.value());
        }
        return decoded.toString();
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        String lookup = lookupPath(request);
        Matcher path = PATH.matcher(lookup);
        boolean kb = isKbUpload(request, lookup);
        String action = kb ? KB_UPLOAD : path.matches() ? action(request.getMethod(), path) : null;
        if (action == null) {
            chain.doFilter(request, response); // other methods on the path: the controller answers
            return;
        }
        boolean upload = kb || AttachmentController.UPLOAD.equals(action);
        Authentication authentication = SecurityContextHolder.getContext().getAuthentication();
        if (!(authentication instanceof JwtAuthenticationToken jwt)) {
            // No valid token: security answers 401. On the upload path, close the connection so its
            // body is never drained (finding 2).
            if (upload) {
                response.setHeader(HttpHeaders.CONNECTION, "close");
            }
            chain.doFilter(request, response);
            return;
        }
        GatewayPrincipal caller = GatewayPrincipal.from(jwt.getToken());
        String conversation = !kb && UUID_PATTERN.matcher(path.group(1)).matches()
                ? path.group(1).toLowerCase(Locale.ROOT) : null;
        // A conversation's files need cap:docs; a knowledge base's documents, the admin role (I1).
        if (kb ? !caller.admin() : !caller.permissions().contains(DOCS_PERMISSION)) {
            refuse(request, response, caller, action, conversation, ErrorCode.FORBIDDEN);
            return;
        }
        if (!upload) {
            chain.doFilter(request, response); // list, status, download: cap:docs is enough at the gate
            return;
        }
        String user = caller.tenantId() + "/" + caller.userId();
        // Every attempt that reaches here counts toward the rate limit, refused or not (finding 2).
        if (!rateLimiter.allow(user)) {
            refuse(request, response, caller, action, conversation, ErrorCode.UPLOAD_RATE_LIMITED);
            return;
        }
        if (request.getContentLengthLong() > limits.maxRequestBytes()) {
            refuse(request, response, caller, action, conversation, ErrorCode.FILE_TOO_LARGE);
            return;
        }
        if (!concurrency.tryAcquire(user)) {
            refuse(request, response, caller, action, conversation, ErrorCode.TOO_MANY_UPLOADS);
            return;
        }
        DeadlineUploadRequest admitted = new DeadlineUploadRequest(
                request, clock.instant().plus(limits.uploadDeadline()), clock, multipart);
        try {
            chain.doFilter(admitted, response);
        } finally {
            admitted.deleteParts();
            concurrency.release(user);
        }
    }

    private static String action(String method, Matcher path) {
        boolean one = path.group(2) != null;
        boolean content = path.group(3) != null;
        return switch (method) {
            case "POST" -> one ? null : AttachmentController.UPLOAD;
            case "GET" -> !one ? AttachmentController.LIST
                    : content ? AttachmentController.DOWNLOAD : AttachmentController.STATUS;
            default -> null;
        };
    }

    private void refuse(HttpServletRequest request, HttpServletResponse response, GatewayPrincipal caller,
            String action, String conversation, ErrorCode code) throws IOException {
        boolean upload = AttachmentController.UPLOAD.equals(action) || KB_UPLOAD.equals(action);
        if (upload) {
            // The refused upload's body is not drained: the socket closes after the response (finding 2).
            response.setHeader(HttpHeaders.CONNECTION, "close");
        }
        if (!audit.reachable()) {
            errors.write(request, response, ErrorCode.AUDIT_UNAVAILABLE);
            return;
        }
        Map<String, Object> detail = new LinkedHashMap<>();
        detail.put("error_code", code.code());
        if (request.getContentLengthLong() >= 0 && upload) {
            detail.put("size", request.getContentLengthLong());
        }
        AuditEvent event = new AuditEvent(RequestIdFilter.requestId(request), caller.userId(), caller.tenantId(),
                action, conversation, AuditStatus.DENIED, detail);
        errors.write(request, response, audit.append(event) ? code : ErrorCode.AUDIT_UNAVAILABLE);
    }
}
