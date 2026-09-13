package sa.ncai.agent.gateway.auth;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

/** Role tier caps become one max_tier: the strictest matching role, never above caps.default (ADR-0046, ADR-0049). */
class TierCapsTest {

    private static final List<String> ORDER = List.of("fast", "standard", "heavy");

    @Test
    void strictestMatchingRoleWins() {
        TierCaps caps = new TierCaps(ORDER,
                Map.of("default", "heavy", "role:admin", "heavy", "role:viewer", "standard", "role:intern", "fast"));

        assertThat(caps.maxTier(List.of("admin", "viewer"))).contains("standard");
        assertThat(caps.maxTier(List.of("viewer", "intern", "cap:general"))).contains("fast");
        assertThat(caps.maxTier(List.of("admin"))).contains("heavy");
    }

    @Test
    void roleCapAboveTheDefaultIsBoundedByTheDefault() {
        TierCaps caps = new TierCaps(ORDER, Map.of("default", "standard", "role:x", "heavy", "role:intern", "fast"));

        assertThat(caps.maxTier(List.of("x"))).contains("standard");
        assertThat(caps.maxTier(List.of("x", "intern"))).contains("fast");
    }

    @Test
    void callerWithoutACappedRoleGetsNoCeiling() {
        TierCaps caps = new TierCaps(ORDER, Map.of("default", "heavy", "role:viewer", "standard"));

        assertThat(caps.maxTier(List.of("user", "default-roles-agent", "cap:viewer"))).isEmpty();
    }

    @Test
    void unknownTierIsRejected() {
        assertThatThrownBy(() -> new TierCaps(ORDER, Map.of("default", "heavy", "role:viewer", "turbo")))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("caps.role:viewer");
    }

    @Test
    void missingDefaultIsRejected() {
        assertThatThrownBy(() -> new TierCaps(ORDER, Map.of("role:viewer", "standard")))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("caps.default");
    }

    @Test
    void repositoryModelsConfigCapsTheViewerAtStandard() {
        Path config = Path.of(System.getenv().getOrDefault("GATEWAY_MODELS_CONFIG", "../../config/models.yaml"));

        assertThat(TierCaps.load(config).maxTier(List.of("user", "viewer"))).contains("standard");
    }
}
