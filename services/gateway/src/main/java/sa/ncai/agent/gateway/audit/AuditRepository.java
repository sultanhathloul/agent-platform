package sa.ncai.agent.gateway.audit;

/** Append-only audit log. */
public interface AuditRepository {

    void append(AuditEvent event);
}
