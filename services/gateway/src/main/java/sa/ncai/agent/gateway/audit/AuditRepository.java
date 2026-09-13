package sa.ncai.agent.gateway.audit;

/** Append-only audit log. */
public interface AuditRepository {

    void append(AuditEvent event);

    /** Throws when the log cannot take a record right now; a turn is refused before it starts (ADR-0042). */
    void check();
}
