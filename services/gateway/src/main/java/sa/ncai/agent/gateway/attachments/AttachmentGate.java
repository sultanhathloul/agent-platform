package sa.ncai.agent.gateway.attachments;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;
import org.springframework.web.filter.OncePerRequestFilter;
import sa.ncai.agent.gateway.api.ErrorCode;
import sa.ncai.agent.gateway.api.ErrorResponses;
import sa.ncai.agent.gateway.api.RequestIdFilter;
import sa.ncai.agent.gateway.audit.AuditEvent;
import sa.ncai.agent.gateway.audit.AuditStatus;
import sa.ncai.agent.gateway.audit.AuditTrail;
import sa.ncai.agent.gateway.auth.GatewayPrincipal;

/**
 * The attachment endpoints' gate (owner decision R3), after authentication and before any request
 * body is read: upload, list, status and download need {@code cap:docs} (403 {@code forbidden}); an
 * upload whose declared length is over the limits of config/docs.yaml is 413 {@code file_too_large}
 * in the JSON envelope, never a reset connection (the server reads and discards the rest, see
 * {@code server.tomcat.max-swallow-size}); and an upload past the caller's per-minute allowance is
 * 429 {@code upload_rate_limited}. Each refusal is audited fail-closed (ADR-0042), like the
 * controller's own.
 */
public class AttachmentGate extends OncePerRequestFilter {

    static final String DOCS_PERMISSION = "cap:docs";
    private static final Pattern PATH =
            Pattern.compile("^/v1/conversations/([^/]+)/attachments(?:/([^/]+)(/content)?)?/?$");
    private static final Pattern UUID_PATTERN =
            Pattern.compile("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$");

    private final DocsLimits limits;
    private final UploadRateLimiter rateLimiter;
    private final AuditTrail audit;
    private final ErrorResponses errors;

    public AttachmentGate(DocsLimits limits, UploadRateLimiter rateLimiter, AuditTrail audit, ErrorResponses errors) {
        this.limits = limits;
        this.rateLimiter = rateLimiter;
        this.audit = audit;
        this.errors = errors;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        return !PATH.matcher(request.getRequestURI()).matches();
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        Matcher path = PATH.matcher(request.getRequestURI());
        String action = path.matches() ? action(request.getMethod(), path) : null;
        Authentication authentication = SecurityContextHolder.getContext().getAuthentication();
        if (action == null || !(authentication instanceof JwtAuthenticationToken jwt)) {
            chain.doFilter(request, response); // other methods, or no token: the controller and security answer
            return;
        }
        GatewayPrincipal caller = GatewayPrincipal.from(jwt.getToken());
        String conversation = UUID_PATTERN.matcher(path.group(1)).matches() ? path.group(1).toLowerCase(Locale.ROOT) : null;
        if (!caller.permissions().contains(DOCS_PERMISSION)) {
            refuse(request, response, caller, action, conversation, ErrorCode.FORBIDDEN);
            return;
        }
        if (AttachmentController.UPLOAD.equals(action)) {
            if (request.getContentLengthLong() > limits.maxRequestBytes()) {
                refuse(request, response, caller, action, conversation, ErrorCode.FILE_TOO_LARGE);
                return;
            }
            if (!rateLimiter.allow(caller.tenantId() + "/" + caller.userId())) {
                refuse(request, response, caller, action, conversation, ErrorCode.UPLOAD_RATE_LIMITED);
                return;
            }
        }
        chain.doFilter(request, response);
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
        if (!audit.reachable()) {
            errors.write(request, response, ErrorCode.AUDIT_UNAVAILABLE);
            return;
        }
        Map<String, Object> detail = new LinkedHashMap<>();
        detail.put("error_code", code.code());
        if (request.getContentLengthLong() >= 0 && AttachmentController.UPLOAD.equals(action)) {
            detail.put("size", request.getContentLengthLong());
        }
        AuditEvent event = new AuditEvent(RequestIdFilter.requestId(request), caller.userId(), caller.tenantId(),
                action, conversation, AuditStatus.DENIED, detail);
        errors.write(request, response, audit.append(event) ? code : ErrorCode.AUDIT_UNAVAILABLE);
    }
}
