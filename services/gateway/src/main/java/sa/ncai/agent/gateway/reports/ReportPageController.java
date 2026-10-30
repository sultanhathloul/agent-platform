package sa.ncai.agent.gateway.reports;

import io.swagger.v3.oas.annotations.Hidden;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;
import sa.ncai.agent.gateway.GatewayProperties;
import sa.ncai.agent.gateway.api.ErrorCode;
import sa.ncai.agent.gateway.api.ErrorResponses;
import sa.ncai.agent.gateway.api.RequestIdFilter;
import sa.ncai.agent.gateway.audit.AuditEvent;
import sa.ncai.agent.gateway.audit.AuditStatus;
import sa.ncai.agent.gateway.audit.AuditTrail;
import sa.ncai.agent.gateway.auth.GatewayPrincipal;
import sa.ncai.agent.gateway.proxy.AgentClient;
import sa.ncai.agent.gateway.proxy.AgentReply;
import sa.ncai.agent.gateway.proxy.AgentUnavailableException;

/**
 * A report version's page for the panel's sandboxed frame (reports PR, I4), by a view link from
 * {@code POST /v1/reports/{id}/versions/{n}/view}: no bearer token, the signed ticket names the caller.
 * A ticket that does not verify or has expired is 404 without a body. The page is the agent's, read as
 * the ticket's caller (the agent checks the owner again), with the kit linked from the gateway's own kit
 * path, and served under a CSP that lets it load the kit and nothing else, be framed by the web only, and
 * run sandboxed to scripts (no same origin, so it can reach neither the gateway's origin nor the web's).
 * Outside the main security chain: no CORS headers, and no {@code X-Frame-Options}, which would forbid the
 * framing that {@code frame-ancestors} allows (SecurityConfig). Each view is audited fail-closed.
 */
@Hidden
@RestController
public class ReportPageController {

    static final String PATH_PREFIX = "/report-views/";
    static final String PATHS = "/report-views/**";
    static final String VIEW = "report.view";

    private static final Logger log = LoggerFactory.getLogger(ReportPageController.class);

    private final AgentClient agent;
    private final AuditTrail audit;
    private final ErrorResponses errors;
    private final ViewTickets tickets;
    private final String kitBase;
    private final String policy;

    public ReportPageController(AgentClient agent, AuditTrail audit, ErrorResponses errors, ViewTickets tickets,
            ReportKit kit, GatewayProperties properties) {
        this.agent = agent;
        this.audit = audit;
        this.errors = errors;
        this.tickets = tickets;
        this.kitBase = ReportController.origin(properties.reports().gatewayPublicUrl()) + kit.path();
        this.policy = policy(kitBase, ReportController.origin(properties.reports().webPublicUrl()));
    }

    /** The page's Content-Security-Policy (I4): the kit and inline styles only, framed by the web, sandboxed. */
    static String policy(String kit, String web) {
        return "default-src 'none'; script-src " + kit + "; style-src " + kit + " 'unsafe-inline'; font-src " + kit
                + "; img-src data:; connect-src 'none'; frame-src 'none'; form-action 'none'; base-uri 'none'; "
                + "frame-ancestors " + web + "; sandbox allow-scripts";
    }

    @GetMapping("/report-views/{ticket}")
    public ResponseEntity<byte[]> page(
            @PathVariable("ticket") String ticket,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        long started = System.nanoTime();
        Optional<ViewTickets.Ticket> granted = tickets.verify(ticket);
        if (granted.isEmpty()) {
            return ResponseEntity.notFound().build();
        }
        ViewTickets.Ticket grant = granted.get();
        if (!audit.reachable()) {
            errors.write(request, response, ErrorCode.AUDIT_UNAVAILABLE);
            return null;
        }
        GatewayPrincipal caller = new GatewayPrincipal(grant.userId(), grant.tenantId(), List.of(), null, false,
                List.of(), null);
        String requestId = RequestIdFilter.requestId(request);
        Map<String, Object> detail = new LinkedHashMap<>();
        detail.put("report_id", grant.reportId());
        detail.put("version", grant.version());
        AgentReply reply;
        try {
            // kit_base holds only an origin's characters and the kit path, none of which a query encodes.
            reply = agent.exchange(HttpMethod.GET, "/v1/reports/" + grant.reportId() + "/versions/" + grant.version()
                    + "/page?form=view&kit_base=" + kitBase, null, caller, requestId);
        } catch (AgentUnavailableException e) {
            log.warn("{}", e.getMessage());
            return refuse(request, response, caller, detail, started, ErrorCode.AGENT_UNAVAILABLE, AuditStatus.ERROR);
        }
        if (reply.status() == 404) {
            return refuse(request, response, caller, detail, started, ErrorCode.REPORT_NOT_FOUND, AuditStatus.DENIED);
        }
        if (reply.status() == 503) {
            return refuse(request, response, caller, detail, started, ErrorCode.REPORT_UNAVAILABLE, AuditStatus.ERROR);
        }
        if (reply.status() != 200) {
            log.warn("agent answered {} to a report page", reply.status());
            return refuse(request, response, caller, detail, started, ErrorCode.AGENT_UNAVAILABLE, AuditStatus.ERROR);
        }
        if (!audit.append(event(requestId, caller, AuditStatus.OK, detail, started))) {
            errors.write(request, response, ErrorCode.AUDIT_UNAVAILABLE);
            return null;
        }
        return ResponseEntity.ok()
                .header(HttpHeaders.CONTENT_TYPE, "text/html; charset=utf-8")
                .header("Content-Security-Policy", policy)
                .header("X-Content-Type-Options", "nosniff")
                .header("Referrer-Policy", "no-referrer")
                .header(HttpHeaders.CACHE_CONTROL, "no-store")
                .body(reply.body());
    }

    private ResponseEntity<byte[]> refuse(HttpServletRequest request, HttpServletResponse response,
            GatewayPrincipal caller, Map<String, Object> detail, long started, ErrorCode code, AuditStatus status)
            throws IOException {
        detail.put("error_code", code.code());
        boolean recorded = audit.append(event(RequestIdFilter.requestId(request), caller, status, detail, started));
        errors.write(request, response, recorded ? code : ErrorCode.AUDIT_UNAVAILABLE);
        return null;
    }

    private static AuditEvent event(String requestId, GatewayPrincipal caller, AuditStatus status,
            Map<String, Object> detail, long started) {
        Map<String, Object> recorded = new LinkedHashMap<>(detail);
        recorded.put("latency_ms", (System.nanoTime() - started) / 1_000_000);
        return new AuditEvent(requestId, caller.userId(), caller.tenantId(), VIEW, null, status, recorded);
    }
}
