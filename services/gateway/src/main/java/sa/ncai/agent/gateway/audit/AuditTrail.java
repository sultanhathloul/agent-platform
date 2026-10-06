package sa.ncai.agent.gateway.audit;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * Fail-closed audit for request/response endpoints (ADR-0042): a request is not served while the
 * log cannot take a record, and the caller learns whether its one record was written.
 */
@Component
public class AuditTrail {

    private static final Logger log = LoggerFactory.getLogger(AuditTrail.class);

    private final AuditRepository audit;

    public AuditTrail(AuditRepository audit) {
        this.audit = audit;
    }

    public boolean reachable() {
        try {
            audit.check();
            return true;
        } catch (RuntimeException e) {
            log.error("audit log unreachable; refusing the request", e);
            return false;
        }
    }

    public boolean append(AuditEvent event) {
        try {
            audit.append(event);
            return true;
        } catch (RuntimeException e) {
            log.error("audit write failed for request {}", event.requestId(), e);
            return false;
        }
    }
}
