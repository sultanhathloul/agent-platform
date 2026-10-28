package sa.ncai.agent.gateway.settings;

import static org.assertj.core.api.Assertions.assertThat;

import java.io.IOException;
import java.nio.file.Path;
import java.time.Duration;
import java.util.List;
import java.util.concurrent.atomic.AtomicReference;
import okhttp3.mockwebserver.Dispatcher;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import sa.ncai.agent.gateway.GatewayProperties;
import sa.ncai.agent.gateway.attachments.DocsLimits;
import sa.ncai.agent.gateway.attachments.UploadConcurrency;
import sa.ncai.agent.gateway.attachments.UploadRateLimiter;
import sa.ncai.agent.gateway.proxy.TurnLimits;

/**
 * The values the gateway enforces itself come from the agent (ADR-0283): read every 5 s, so a
 * change reaches the gateway within 10 s; the last values stay in force when the agent cannot be
 * read; before the first read, the files'. Hot paths read the cache only.
 */
class PlatformLimitsTest {

    private static final Path TURN = Path.of("src/test/resources/turn-test.yaml");
    private final MockWebServer agent = new MockWebServer();
    private final AtomicReference<String> answer = new AtomicReference<>(limits(1, 120, 20, 2));
    private final AtomicReference<Integer> status = new AtomicReference<>(200);

    @BeforeEach
    void start() throws IOException {
        agent.setDispatcher(new Dispatcher() {
            @Override
            public MockResponse dispatch(RecordedRequest request) {
                if (!"test-internal-token".equals(request.getHeader("X-Internal-Token"))) {
                    return new MockResponse().setResponseCode(401);
                }
                return new MockResponse().setResponseCode(status.get())
                        .setHeader("Content-Type", "application/json").setBody(answer.get());
            }
        });
        agent.start();
    }

    @AfterEach
    void stop() throws IOException {
        agent.shutdown();
    }

    private static String limits(long version, double deadline, int perMinute, int concurrent) {
        return "{\"version\":%d,\"deadline_s\":%s,\"uploads_per_minute\":%d,\"max_concurrent_uploads\":%d}"
                .formatted(version, deadline, perMinute, concurrent);
    }

    private PlatformLimits platform(Duration refresh) {
        GatewayProperties properties = new GatewayProperties(null,
                new GatewayProperties.Agent("http://" + agent.getHostName() + ":" + agent.getPort(),
                        "test-internal-token", Duration.ofSeconds(2), Duration.ofSeconds(5), Duration.ofSeconds(30)),
                null, null, null, null, null, new GatewayProperties.Settings(refresh), null);
        DocsLimits docs = DocsLimits.load(Path.of(System.getenv().getOrDefault("GATEWAY_DOCS_CONFIG", "../../config/docs.yaml")));
        return new PlatformLimits(properties, TurnLimits.load(TURN), docs);
    }

    @Test
    void beforeTheFirstReadTheFilesValuesAreInForce() {
        PlatformLimits limits = platform(Duration.ZERO);
        TurnLimits file = TurnLimits.load(TURN);
        assertThat(limits.relayBound()).isEqualTo(file.relayBound());
        assertThat(limits.readAt()).isNull();
        assertThat(agent.getRequestCount()).isZero();
    }

    @Test
    void aReadSetsTheValuesAndAFailedReadKeepsTheLastOnes() {
        PlatformLimits limits = platform(Duration.ZERO);
        TurnLimits file = TurnLimits.load(TURN);
        answer.set(limits(7, 150, 5, 1));
        assertThat(limits.refresh()).isTrue();
        assertThat(limits.relayBound()).isEqualTo(Duration.ofSeconds(150).plus(file.gatewayMargin()));
        assertThat(limits.uploadsPerMinute()).isEqualTo(5);
        assertThat(limits.maxConcurrentUploads()).isEqualTo(1);
        status.set(503);
        assertThat(limits.refresh()).isFalse();
        assertThat(limits.limits().version()).isEqualTo(7);
        status.set(200);
        answer.set(limits(8, 0, 5, 1));  // not usable: kept out
        assertThat(limits.refresh()).isFalse();
        assertThat(limits.limits().version()).isEqualTo(7);
    }

    @Test
    void theProfileDeadlinesComeFromTheAgentAndOtherwiseFromTheFile() {
        PlatformLimits limits = platform(Duration.ZERO);
        TurnLimits file = TurnLimits.load(TURN);
        // turn-test.yaml: deadline 1 s, profile report 3 s, margin 1 s
        assertThat(limits.longestDeadline()).isEqualTo(Duration.ofSeconds(3));
        assertThat(limits.streamCeiling()).isEqualTo(Duration.ofSeconds(3).plus(file.gatewayMargin()));
        assertThat(limits.relayBound(300.0)).isEqualTo(Duration.ofSeconds(4));
        answer.set("{\"version\":3,\"deadline_s\":120,\"uploads_per_minute\":20,\"max_concurrent_uploads\":2,"
                + "\"profile_deadlines_s\":{\"report\":300.0}}");
        assertThat(limits.refresh()).isTrue();
        assertThat(limits.relayBound(300.0)).isEqualTo(Duration.ofSeconds(301));
        assertThat(limits.relayBound(900.0)).isEqualTo(Duration.ofSeconds(301));
        assertThat(limits.relayBound((Double) null)).isEqualTo(Duration.ofSeconds(121));
        // An agent from before the profiles: the file's stay in force.
        answer.set(limits(4, 120, 20, 2));
        assertThat(limits.refresh()).isTrue();
        assertThat(limits.longestDeadline()).isEqualTo(Duration.ofSeconds(120));
        assertThat(limits.limits().profileDeadlinesS()).containsEntry("report", 3.0);
        // A profile without a usable deadline: the whole answer is kept out.
        answer.set("{\"version\":5,\"deadline_s\":120,\"uploads_per_minute\":20,\"max_concurrent_uploads\":2,"
                + "\"profile_deadlines_s\":{\"report\":0}}");
        assertThat(limits.refresh()).isFalse();
        assertThat(limits.limits().version()).isEqualTo(4);
    }

    @Test
    void anUploadCapChangeReachesTheGatewayWithinTenSeconds() throws Exception {
        PlatformLimits limits = platform(Duration.ofSeconds(5));
        try {
            UploadRateLimiter rate = new UploadRateLimiter(limits::uploadsPerMinute, java.time.Clock.systemUTC());
            UploadConcurrency concurrency = new UploadConcurrency(limits::maxConcurrentUploads, 50);
            long until = System.nanoTime() + Duration.ofSeconds(3).toNanos();
            while (limits.readAt() == null && System.nanoTime() < until) {
                Thread.sleep(20);
            }
            assertThat(limits.uploadsPerMinute()).isEqualTo(20);
            answer.set(limits(2, 120, 3, 1));
            long changed = System.nanoTime();
            while (limits.uploadsPerMinute() != 3 && System.nanoTime() - changed < Duration.ofSeconds(11).toNanos()) {
                Thread.sleep(50);
            }
            double seconds = (System.nanoTime() - changed) / 1e9;
            assertThat(limits.uploadsPerMinute()).isEqualTo(3);
            assertThat(seconds).isLessThan(10.0);
            List<Boolean> allowed = List.of(rate.allow("u"), rate.allow("u"), rate.allow("u"), rate.allow("u"));
            assertThat(allowed).containsExactly(true, true, true, false);
            assertThat(concurrency.tryAcquire("u")).isTrue();
            assertThat(concurrency.tryAcquire("u")).isFalse();
            System.out.printf("9 upload cap: the change reached the gateway in %.2f s (bound 10 s)%n", seconds);
        } finally {
            limits.stop();
        }
    }
}
