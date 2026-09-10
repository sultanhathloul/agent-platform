package sa.ncai.agent.gateway;

import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import sa.ncai.agent.gateway.audit.AuditEvent;
import sa.ncai.agent.gateway.audit.AuditRepository;

public class InMemoryAuditRepository implements AuditRepository {

    private final List<AuditEvent> events = new CopyOnWriteArrayList<>();

    @Override
    public void append(AuditEvent event) {
        events.add(event);
    }

    public List<AuditEvent> events() {
        return List.copyOf(events);
    }

    public void clear() {
        events.clear();
    }
}
