package sa.ncai.agent.gateway.proxy;

import java.io.IOException;
import java.io.Reader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.Map;
import org.yaml.snakeyaml.LoaderOptions;
import org.yaml.snakeyaml.Yaml;
import org.yaml.snakeyaml.constructor.SafeConstructor;

/**
 * The whole-turn deadline from {@code config/turn.yaml}, the file the agent reads too (timeout
 * budget PR, I2 and I3, ADR-0255, ADR-0256). The gateway uses one bound from it: a relayed turn
 * stream ends at {@code deadline_s + gateway_margin_s}, so the agent's own {@code turn_timeout}
 * always reaches the user first and a stream the agent never ends is still cut.
 */
public record TurnLimits(Duration deadline, Duration gatewayMargin) {

    /** One total bound on a relayed turn, from the request to its last byte. */
    public Duration relayBound() {
        return deadline.plus(gatewayMargin);
    }

    public static TurnLimits load(Path path) {
        try (Reader reader = Files.newBufferedReader(path, StandardCharsets.UTF_8)) {
            Map<?, ?> config = new Yaml(new SafeConstructor(new LoaderOptions())).load(reader);
            return new TurnLimits(seconds(config, "deadline_s"), seconds(config, "gateway_margin_s"));
        } catch (IOException | RuntimeException e) {
            throw new IllegalStateException("cannot read the turn deadline from " + path + ": " + e.getMessage(), e);
        }
    }

    private static Duration seconds(Map<?, ?> config, String key) {
        double value = ((Number) config.get(key)).doubleValue();
        if (value <= 0) {
            throw new IllegalArgumentException(key + " must be positive");
        }
        return Duration.ofMillis(Math.round(value * 1000));
    }
}
