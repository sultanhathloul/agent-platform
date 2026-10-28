package sa.ncai.agent.gateway.proxy;

import java.io.IOException;
import java.io.Reader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.Map;
import org.yaml.snakeyaml.LoaderOptions;
import org.yaml.snakeyaml.Yaml;
import org.yaml.snakeyaml.constructor.SafeConstructor;

/**
 * The whole-turn deadline from {@code config/turn.yaml}, the file the agent reads too (timeout
 * budget PR, I2 and I3, ADR-0255, ADR-0256). The gateway uses two bounds from it: the agent's
 * response headers within {@code gateway_headers_s} (past the agent's admission bound, ADR-0264),
 * and a relayed turn stream ends at {@code deadline_s + gateway_margin_s}, so the agent's own
 * {@code turn_timeout} always reaches the user first and a stream the agent never ends is still cut.
 *
 * <p>{@code profiles} (reports PR, ADR-0379): a turn may run under a profile's longer deadline, which
 * the agent chooses at admission and declares in its response headers ({@code X-Turn-Deadline-S}); the
 * relay then ends that stream at the profile's deadline + {@code gateway_margin_s}, never past the
 * longest deadline the file names.
 */
public record TurnLimits(
        Duration deadline, Duration gatewayMargin, Duration headers, Map<String, Duration> profileDeadlines) {

    public TurnLimits {
        profileDeadlines = Map.copyOf(profileDeadlines);
    }

    /** The header bound when the file names none (the PR #12 review, decision 5). */
    static final Duration DEFAULT_HEADERS = Duration.ofSeconds(15);

    /** One total bound on a relayed turn, from the request to its last byte. */
    public Duration relayBound() {
        return deadline.plus(gatewayMargin);
    }

    public static TurnLimits load(Path path) {
        try (Reader reader = Files.newBufferedReader(path, StandardCharsets.UTF_8)) {
            Map<?, ?> config = new Yaml(new SafeConstructor(new LoaderOptions())).load(reader);
            Duration headers = config.get("gateway_headers_s") == null
                    ? DEFAULT_HEADERS
                    : seconds(config, "gateway_headers_s");
            return new TurnLimits(seconds(config, "deadline_s"), seconds(config, "gateway_margin_s"), headers,
                    profiles(config.get("profiles")));
        } catch (IOException | RuntimeException e) {
            throw new IllegalStateException("cannot read the turn deadline from " + path + ": " + e.getMessage(), e);
        }
    }

    /** Each profile's {@code deadline_s}, by name; none when the file names no profile. */
    private static Map<String, Duration> profiles(Object section) {
        Map<String, Duration> deadlines = new LinkedHashMap<>();
        if (section == null) {
            return deadlines;
        }
        ((Map<?, ?>) section).forEach((name, profile) ->
                deadlines.put(String.valueOf(name), seconds((Map<?, ?>) profile, "deadline_s")));
        return deadlines;
    }

    private static Duration seconds(Map<?, ?> config, String key) {
        double value = ((Number) config.get(key)).doubleValue();
        if (value <= 0) {
            throw new IllegalArgumentException(key + " must be positive");
        }
        return Duration.ofMillis(Math.round(value * 1000));
    }
}
