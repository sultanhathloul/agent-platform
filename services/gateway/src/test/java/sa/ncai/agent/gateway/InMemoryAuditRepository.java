package sa.ncai.agent.gateway;

import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import sa.ncai.agent.gateway.audit.AuditEvent;
import sa.ncai.agent.gateway.audit.AuditRepository;

public class InMemoryAuditRepository implements AuditRepository {

    /** How the fake store behaves: working, unreachable, or reachable but failing every write. */
    public enum Mode {
        AVAILABLE,
        DOWN,
        WRITES_FAIL
    }

    private final List<AuditEvent> events = new CopyOnWriteArrayList<>();
    private volatile Mode mode = Mode.AVAILABLE;

    @Override
    public void append(AuditEvent event) {
        if (mode != Mode.AVAILABLE) {
            throw new IllegalStateException("audit store unavailable (test)");
        }
        events.add(event);
    }

    @Override
    public void check() {
        if (mode == Mode.DOWN) {
            throw new IllegalStateException("audit store unreachable (test)");
        }
    }

    public List<AuditEvent> events() {
        return List.copyOf(events);
    }

    public void setMode(Mode mode) {
        this.mode = mode;
    }

    public void clear() {
        events.clear();
        mode = Mode.AVAILABLE;
    }
}
