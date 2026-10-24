package sa.ncai.agent.gateway.settings;

import static org.assertj.core.api.Assertions.assertThat;

import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneId;
import java.time.ZoneOffset;
import org.junit.jupiter.api.Test;

/** ADR-0287: a page session's first usage read gets a row, then at most one row per 10 minutes per admin. */
class UsageAuditGateTest {

    private static final class MovingClock extends Clock {
        Instant now = Instant.parse("2026-10-24T09:00:00Z");

        @Override
        public ZoneId getZone() {
            return ZoneOffset.UTC;
        }

        @Override
        public Clock withZone(ZoneId zone) {
            return this;
        }

        @Override
        public Instant instant() {
            return now;
        }
    }

    private final MovingClock clock = new MovingClock();
    private final UsageAuditGate gate = new UsageAuditGate(clock);

    @Test
    void aPageSessionsFirstReadThenOneRowPerTenMinutes() {
        assertThat(gate.claim("admin-1", "page-a")).isNotNull();
        // The page reads every 5 s: no row for the next ten minutes.
        for (int read = 1; read < 120; read++) {
            clock.now = clock.now.plusSeconds(5);
            assertThat(gate.claim("admin-1", "page-a")).as("read %d", read).isNull();
        }
        clock.now = Instant.parse("2026-10-24T09:10:00Z");
        assertThat(gate.claim("admin-1", "page-a")).isNotNull();
        clock.now = clock.now.plus(Duration.ofMinutes(9));
        assertThat(gate.claim("admin-1", "page-a")).isNull();
    }

    @Test
    void aNewPageSessionGetsItsRowAtOnceAndEachAdminHasItsOwnInterval() {
        assertThat(gate.claim("admin-1", "page-a")).isNotNull();
        clock.now = clock.now.plusSeconds(5);
        assertThat(gate.claim("admin-1", "page-b")).isNotNull();
        assertThat(gate.claim("admin-2", "page-c")).isNotNull();
        clock.now = clock.now.plusSeconds(5);
        assertThat(gate.claim("admin-1", "page-a")).isNull();
        assertThat(gate.claim("admin-1", "page-b")).isNull();
        assertThat(gate.claim("admin-2", "page-c")).isNull();
    }

    @Test
    void aReadWithoutAPageSessionIsDueByTheIntervalOnly() {
        assertThat(gate.claim("admin-1", null)).isNotNull();
        clock.now = clock.now.plusSeconds(5);
        assertThat(gate.claim("admin-1", null)).isNull();
        clock.now = clock.now.plus(UsageAuditGate.INTERVAL);
        assertThat(gate.claim("admin-1", null)).isNotNull();
    }

    @Test
    void aRowThatFailedToWriteIsDueAgainOnTheNextRead() {
        UsageAuditGate.Claim claim = gate.claim("admin-1", "page-a");
        gate.release(claim);
        assertThat(gate.claim("admin-1", "page-a")).isNotNull();
        assertThat(gate.claim("admin-1", "page-a")).isNull();
    }

    @Test
    void anAdminKeepsOnlyItsLatestPageSessions() {
        for (int page = 0; page <= UsageAuditGate.SESSIONS; page++) {
            assertThat(gate.claim("admin-1", "page-" + page)).isNotNull();
        }
        // The oldest was dropped: seen again, it counts as new; the latest is still known.
        assertThat(gate.claim("admin-1", "page-0")).isNotNull();
        assertThat(gate.claim("admin-1", "page-" + UsageAuditGate.SESSIONS)).isNull();
    }
}
