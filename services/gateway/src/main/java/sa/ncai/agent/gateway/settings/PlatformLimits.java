package sa.ncai.agent.gateway.settings;

import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.annotation.PreDestroy;
import java.net.http.HttpClient;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import sa.ncai.agent.gateway.GatewayProperties;
import sa.ncai.agent.gateway.attachments.DocsLimits;
import sa.ncai.agent.gateway.proxy.AgentClient;
import sa.ncai.agent.gateway.proxy.TurnLimits;

/**
 * The platform settings the gateway enforces itself, in force at the agent (owner decision
 * 2026-10-22, ADR-0283): a turn's deadline (the relay ends a stream at it plus
 * {@code gateway_margin_s}), uploads per user per minute, and uploads in flight per user. An
 * administrator changes them on the settings page; the agent holds the overrides.
 *
 * <p>Read from the agent's {@code GET /v1/internal/settings/limits} every {@code refresh} (5 s),
 * each read bounded by {@link #FETCH_BOUND} (4 s) from request to body, so a change reaches the
 * gateway within 10 s. Hot paths read the values cached here, never the agent. When the agent
 * cannot be read, the last values read stay in force; before the first read, the files' values
 * (config/turn.yaml and config/docs.yaml, with GATEWAY_DOCS_UPLOADS_PER_MINUTE). A refresh of
 * zero turns the reads off (the test profile, whose agent is a scripted mock).
 *
 * <p>The deadline profiles (reports PR, ADR-0379) come the same way, as {@code profile_deadlines_s};
 * an answer without them (an agent from before them) keeps config/turn.yaml's. A turn stream's bound
 * is the deadline its agent declared, capped at the longest of them all, plus the margin.
 */
@Component
public class PlatformLimits {

    /** One read from request to body, inside the refresh interval (CLAUDE.md rule 12c). */
    static final Duration FETCH_BOUND = Duration.ofSeconds(4);

    private static final Logger LOG = LoggerFactory.getLogger(PlatformLimits.class);

    /** What the agent answers; also what is cached, with when it was read. */
    public record Limits(
            @JsonProperty("version") long version,
            @JsonProperty("deadline_s") double deadlineS,
            @JsonProperty("uploads_per_minute") int uploadsPerMinute,
            @JsonProperty("max_concurrent_uploads") int maxConcurrentUploads,
            @JsonProperty("profile_deadlines_s") Map<String, Double> profileDeadlinesS) {}

    private record Cached(Limits limits, Instant readAt) {}

    private final AtomicReference<Cached> current;
    private final Duration margin;
    private final Duration headers;
    /** config/turn.yaml's profile deadlines, in force until the agent names its own. */
    private final Map<String, Double> fileProfiles;
    private final RestClient client;
    private final String internalToken;
    private final Clock clock;
    private final ScheduledExecutorService scheduler;

    @Autowired
    public PlatformLimits(GatewayProperties properties, TurnLimits turn, DocsLimits docs) {
        this(properties, turn, docs, Clock.systemUTC());
    }

    PlatformLimits(GatewayProperties properties, TurnLimits turn, DocsLimits docs, Clock clock) {
        this.clock = clock;
        this.margin = turn.gatewayMargin();
        this.headers = turn.headers();
        Map<String, Double> profiles = new LinkedHashMap<>();
        turn.profileDeadlines().forEach((name, deadline) -> profiles.put(name, deadline.toMillis() / 1000.0));
        this.fileProfiles = Map.copyOf(profiles);
        this.current = new AtomicReference<>(new Cached(
                new Limits(0, turn.deadline().toMillis() / 1000.0, docs.uploadsPerMinute(), docs.maxConcurrentUploads(),
                        fileProfiles),
                null));
        HttpClient http = HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_1_1)
                .connectTimeout(properties.agent().connectTimeout())
                .build();
        JdkClientHttpRequestFactory factory = new JdkClientHttpRequestFactory(http);
        factory.setReadTimeout(FETCH_BOUND);
        this.client = RestClient.builder().baseUrl(properties.agent().baseUrl()).requestFactory(factory).build();
        this.internalToken = properties.agent().internalToken();
        Duration refresh = properties.settings() == null ? Duration.ofSeconds(5) : properties.settings().refresh();
        if (refresh.isZero() || refresh.isNegative()) {
            this.scheduler = null;
            return;
        }
        this.scheduler = Executors.newSingleThreadScheduledExecutor(runnable -> {
            Thread thread = new Thread(runnable, "platform-limits");
            thread.setDaemon(true);
            return thread;
        });
        scheduler.scheduleWithFixedDelay(this::refresh, 0, refresh.toMillis(), TimeUnit.MILLISECONDS);
    }

    /** Reads the agent's values once; on any failure the values in force stay (logged). */
    public boolean refresh() {
        try {
            Limits read = client.get()
                    .uri("/v1/internal/settings/limits")
                    .header(AgentClient.INTERNAL_TOKEN_HEADER, internalToken)
                    .retrieve()
                    .body(Limits.class);
            if (read == null || read.deadlineS() <= 0 || read.uploadsPerMinute() < 1 || read.maxConcurrentUploads() < 1
                    || (read.profileDeadlinesS() != null
                            && read.profileDeadlinesS().values().stream().anyMatch(d -> d == null || d <= 0))) {
                LOG.warn("platform limits: the agent's answer is not usable; the last values stay in force");
                return false;
            }
            if (read.profileDeadlinesS() == null) {
                read = new Limits(read.version(), read.deadlineS(), read.uploadsPerMinute(),
                        read.maxConcurrentUploads(), fileProfiles);
            }
            Cached before = current.getAndSet(new Cached(read, clock.instant()));
            if (before.limits().version() != read.version()) {
                LOG.info("platform limits: version {} in force", read.version());
            }
            return true;
        } catch (RuntimeException e) {
            LOG.warn("platform limits: the agent could not be read ({}); the last values stay in force",
                    e.getClass().getSimpleName());
            return false;
        }
    }

    public Limits limits() {
        return current.get().limits();
    }

    /** When the values in force were read from the agent; null for the files' values. */
    public Instant readAt() {
        return current.get().readAt();
    }

    /** The relay's one bound on a turn stream: the deadline in force plus gateway_margin_s (I3). */
    public Duration relayBound() {
        return seconds(limits().deadlineS()).plus(margin);
    }

    /**
     * The relay's bound on a stream whose agent declared {@code declaredS} seconds as its turn's
     * deadline ({@code X-Turn-Deadline-S}, ADR-0379): that deadline, at most the longest in force, plus
     * gateway_margin_s; without a usable declaration, {@link #relayBound()}.
     */
    public Duration relayBound(Double declaredS) {
        if (declaredS == null || !(declaredS > 0) || declaredS.isInfinite()) {
            return relayBound();
        }
        Duration declared = seconds(declaredS);
        Duration longest = longestDeadline();
        return (declared.compareTo(longest) < 0 ? declared : longest).plus(margin);
    }

    /** The longest deadline a turn may run under: the deadline in force or a profile's, whichever is longer. */
    public Duration longestDeadline() {
        Limits limits = limits();
        double longest = limits.deadlineS();
        for (double profile : limits.profileDeadlinesS().values()) {
            longest = Math.max(longest, profile);
        }
        return seconds(longest);
    }

    /**
     * The ceiling on any turn stream, whatever its agent declares: the longest deadline plus
     * gateway_margin_s, so no turn's own bound is ever longer (ADR-0379).
     */
    public Duration streamCeiling() {
        return longestDeadline().plus(margin);
    }

    private static Duration seconds(double value) {
        return Duration.ofMillis(Math.round(value * 1000));
    }

    /** The agent's response headers within gateway_headers_s (ADR-0264); a redeploy changes it. */
    public Duration headers() {
        return headers;
    }

    public int uploadsPerMinute() {
        return limits().uploadsPerMinute();
    }

    public int maxConcurrentUploads() {
        return limits().maxConcurrentUploads();
    }

    @PreDestroy
    void stop() {
        if (scheduler != null) {
            scheduler.shutdownNow();
        }
    }
}
