package sa.ncai.agent.gateway.attachments;

import java.time.Clock;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.concurrent.ConcurrentHashMap;

/**
 * At most {@code perMinute} uploads per user in any sliding minute (owner decision R3), in this
 * gateway's memory: one gateway process in v1, as for the turn lock's pool (ADR-0109).
 */
public final class UploadRateLimiter {

    private static final long WINDOW_MILLIS = 60_000;

    private final int perMinute;
    private final Clock clock;
    private final ConcurrentHashMap<String, Deque<Long>> recent = new ConcurrentHashMap<>();

    public UploadRateLimiter(int perMinute, Clock clock) {
        this.perMinute = perMinute;
        this.clock = clock;
    }

    /** Counts the upload and says whether it is allowed. */
    public boolean allow(String user) {
        long now = clock.millis();
        Deque<Long> times = recent.computeIfAbsent(user, key -> new ArrayDeque<>());
        synchronized (times) {
            while (!times.isEmpty() && times.peekFirst() <= now - WINDOW_MILLIS) {
                times.pollFirst();
            }
            if (times.size() >= perMinute) {
                return false;
            }
            times.addLast(now);
            return true;
        }
    }

    /** Tests only: forget every user's uploads. */
    public void clear() {
        recent.clear();
    }
}
