package sa.ncai.agent.gateway.attachments;

import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * At most {@code maxPerUser} uploads in flight per user at once (owner decision review round 2,
 * finding 2), in this gateway's memory: one gateway process in v1, as for the rate limiter and the
 * turn lock's pool (ADR-0109). A slot is held for the whole request — while its body is read — and
 * released when the request completes, so a user cannot pin many connections open with slow bodies.
 */
public final class UploadConcurrency {

    private final int maxPerUser;
    private final ConcurrentHashMap<String, AtomicInteger> inFlight = new ConcurrentHashMap<>();

    public UploadConcurrency(int maxPerUser) {
        this.maxPerUser = maxPerUser;
    }

    /** Takes a slot for the user and says whether one was free; release it with {@link #release}. */
    public boolean tryAcquire(String user) {
        AtomicInteger count = inFlight.computeIfAbsent(user, key -> new AtomicInteger());
        if (count.incrementAndGet() > maxPerUser) {
            count.decrementAndGet();
            return false;
        }
        return true;
    }

    public void release(String user) {
        AtomicInteger count = inFlight.get(user);
        if (count != null) {
            count.decrementAndGet();
        }
    }

    /** Tests only: forget every user's in-flight count. */
    public void clear() {
        inFlight.clear();
    }
}
