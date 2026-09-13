package sa.ncai.agent.gateway.audit;

import java.util.Locale;

public enum AuditStatus {
    OK,
    ERROR,
    DENIED,
    ABORTED;

    /** Value stored in {@code gateway.audit_events.status}. */
    public String dbValue() {
        return name().toLowerCase(Locale.ROOT);
    }
}
