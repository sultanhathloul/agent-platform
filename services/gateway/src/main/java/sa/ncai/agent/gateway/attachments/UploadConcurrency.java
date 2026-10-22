package sa.ncai.agent.gateway.attachments;

import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.IntSupplier;

/**
 * At most {@code maxPerUser} uploads in flight per user at once (owner decision review round 2,
 * finding 2), and at most {@code maxTotal} across the whole gateway behind it (the verification of
 * round 2), in this gateway's memory: one gateway process in v1, as for the rate limiter and the
 * turn lock's pool (ADR-0109). A slot is held for the whole request — while its body is read — and
 * released when the request completes, so neither one user nor all uploads together can pin more
 * than these many connections open with slow bodies. The per-user limit is read on every upload from
 * the platform settings cached in the gateway (ADR-0283): lowered, the uploads in flight keep their
 * slots and the next one is refused until they end.
 */
public final class UploadConcurrency {

    private final IntSupplier maxPerUser;
    private final int maxTotal;
    private final ConcurrentHashMap<String, AtomicInteger> inFlight = new ConcurrentHashMap<>();
    private final AtomicInteger total = new AtomicInteger();

    public UploadConcurrency(int maxPerUser, int maxTotal) {
        this(() -> maxPerUser, maxTotal);
    }

    public UploadConcurrency(IntSupplier maxPerUser, int maxTotal) {
        this.maxPerUser = maxPerUser;
        this.maxTotal = maxTotal;
    }

    /**
     * Takes a slot for the user, and one of the gateway's, and says whether both were free; release
     * them with {@link #release}.
     */
    public boolean tryAcquire(String user) {
        AtomicInteger count = inFlight.computeIfAbsent(user, key -> new AtomicInteger());
        if (count.incrementAndGet() > maxPerUser.getAsInt()) {
            count.decrementAndGet();
            return false;
        }
        if (total.incrementAndGet() > maxTotal) {
            total.decrementAndGet();
            count.decrementAndGet();
            return false;
        }
        return true;
    }

    public void release(String user) {
        AtomicInteger count = inFlight.get(user);
        if (count != null) {
            count.decrementAndGet();
            total.decrementAndGet();
        }
    }

    /** Tests only: forget every user's in-flight count. */
    public void clear() {
        inFlight.clear();
        total.set(0);
    }
}
