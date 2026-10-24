package sa.ncai.agent.gateway.settings;

import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Set;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

/**
 * Which usage reads get an audit row (ADR-0287): the first read of a page session, then at most one
 * row per {@link #INTERVAL} per admin. The page reads every 5 s; a row per read would bury the audit
 * log. A refused read and every settings change are audited on every call, never through here.
 * Bounded: {@link #ADMINS} admins, each with its {@link #SESSIONS} latest page sessions.
 */
@Component
public class UsageAuditGate {

    static final Duration INTERVAL = Duration.ofMinutes(10);
    static final int ADMINS = 1024;
    static final int SESSIONS = 32;

    /** What a claimed row changed, so a row that failed to write is claimed again on the next read. */
    public record Claim(String admin, String session, Instant previous, boolean newSession) {}

    private static final class State {
        Instant lastRow;
        final Set<String> sessions = new LinkedHashSet<>();
    }

    private final Clock clock;
    private final Map<String, State> admins = new LinkedHashMap<>(16, 0.75f, true) {
        @Override
        protected boolean removeEldestEntry(Map.Entry<String, State> eldest) {
            return size() > ADMINS;
        }
    };

    @Autowired
    public UsageAuditGate() {
        this(Clock.systemUTC());
    }

    UsageAuditGate(Clock clock) {
        this.clock = clock;
    }

    /**
     * A claim when this read is due a row: its page session is new for this admin, or the admin's
     * last row is {@link #INTERVAL} old; null otherwise. A read without a page session is due by
     * the interval only.
     */
    public synchronized Claim claim(String admin, String session) {
        Instant now = clock.instant();
        State state = admins.computeIfAbsent(admin, k -> new State());
        boolean newSession = session != null && !state.sessions.contains(session);
        boolean due = newSession || state.lastRow == null || !now.isBefore(state.lastRow.plus(INTERVAL));
        if (!due) {
            return null;
        }
        Claim claim = new Claim(admin, session, state.lastRow, newSession);
        state.lastRow = now;
        if (newSession) {
            state.sessions.add(session);
            if (state.sessions.size() > SESSIONS) {
                state.sessions.remove(state.sessions.iterator().next());
            }
        }
        return claim;
    }

    /** The claimed row was not written: the next read is due again. */
    public synchronized void release(Claim claim) {
        State state = admins.get(claim.admin());
        if (state == null) {
            return;
        }
        state.lastRow = claim.previous();
        if (claim.newSession()) {
            state.sessions.remove(claim.session());
        }
    }
}
