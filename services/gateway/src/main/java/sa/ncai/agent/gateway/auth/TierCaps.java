package sa.ncai.agent.gateway.auth;

import java.io.IOException;
import java.io.Reader;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Collection;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import org.yaml.snakeyaml.LoaderOptions;
import org.yaml.snakeyaml.Yaml;
import org.yaml.snakeyaml.constructor.SafeConstructor;

/**
 * Model tier ceilings by realm role, from the {@code order}, {@code caps.default} and
 * {@code caps.role:<name>} entries of config/models.yaml (README §19.1). The gateway turns the
 * caller's roles into one {@code principal.max_tier}; the agent never sees roles (ADR-0046). Caps
 * are ceilings: a role cap never exceeds {@code caps.default} (ADR-0049).
 */
public final class TierCaps {

    private static final String ROLE_PREFIX = "role:";
    private static final String DEFAULT = "default";

    private final List<String> order;
    private final String defaultTier;
    private final Map<String, String> byRole;

    public TierCaps(List<String> order, Map<String, String> caps) {
        this.order = List.copyOf(order);
        Map<String, String> roles = new LinkedHashMap<>();
        caps.forEach((key, tier) -> {
            if (!this.order.contains(tier)) {
                throw new IllegalArgumentException("caps." + key + ": unknown tier " + tier + "; known: " + this.order);
            }
            if (key.startsWith(ROLE_PREFIX)) {
                roles.put(key.substring(ROLE_PREFIX.length()), tier);
            }
        });
        this.defaultTier = caps.get(DEFAULT);
        if (defaultTier == null) {
            throw new IllegalArgumentException("caps.default is required: it bounds every role cap");
        }
        this.byRole = Map.copyOf(roles);
    }

    public static TierCaps load(Path modelsConfig) {
        try (Reader reader = Files.newBufferedReader(modelsConfig, StandardCharsets.UTF_8)) {
            Map<?, ?> config = new Yaml(new SafeConstructor(new LoaderOptions())).load(reader);
            return new TierCaps(strings(config.get("order"), modelsConfig), tiers(config.get("caps"), modelsConfig));
        } catch (IOException e) {
            throw new UncheckedIOException("cannot read " + modelsConfig, e);
        }
    }

    /**
     * The strictest ceiling among the caller's roles, never above {@code caps.default}; empty when
     * none of the roles has a cap, and the agent applies {@code caps.default} itself.
     */
    public Optional<String> maxTier(Collection<String> realmRoles) {
        Comparator<String> rank = Comparator.comparingInt(order::indexOf);
        return realmRoles.stream()
                .map(byRole::get)
                .filter(Objects::nonNull)
                .min(rank)
                .map(tier -> rank.compare(tier, defaultTier) <= 0 ? tier : defaultTier);
    }

    private static List<String> strings(Object value, Path source) {
        if (!(value instanceof List<?> list) || !list.stream().allMatch(String.class::isInstance)) {
            throw new IllegalArgumentException(source + ": `order` must be a list of tier names");
        }
        return list.stream().map(String.class::cast).toList();
    }

    private static Map<String, String> tiers(Object value, Path source) {
        if (!(value instanceof Map<?, ?> map)) {
            throw new IllegalArgumentException(source + ": `caps` must be a mapping");
        }
        Map<String, String> caps = new LinkedHashMap<>();
        map.forEach((key, tier) -> caps.put(String.valueOf(key), String.valueOf(tier)));
        return caps;
    }
}
