package sa.ncai.agent.gateway.attachments;

import static org.assertj.core.api.Assertions.assertThat;

import java.nio.file.Path;
import java.util.HashMap;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.springframework.boot.context.properties.bind.Binder;
import org.springframework.boot.context.properties.source.ConfigurationPropertySources;
import org.springframework.core.env.MutablePropertySources;
import org.springframework.core.env.SystemEnvironmentPropertySource;
import sa.ncai.agent.gateway.GatewayProperties;

/**
 * The per-user upload rate's strict default (the verification of review round 2): compose passes
 * {@code GATEWAY_DOCS_UPLOADS_PER_MINUTE} from {@code UPLOADS_PER_MINUTE} with no fallback, so when
 * .env does not set it the variable is absent or empty, and the gateway uses config/docs.yaml's
 * baseline, 20. Bound from environment variables, as in the container.
 */
class UploadRateDefaultTest {

    private static final String SHIPPED =
            System.getenv().getOrDefault("GATEWAY_DOCS_CONFIG", "../../config/docs.yaml");

    @Test
    void theShippedBaselineIs20() {
        assertThat(DocsLimits.load(Path.of(SHIPPED)).uploadsPerMinute()).isEqualTo(20);
    }

    @Test
    void unsetTheEffectiveLimitIs20() {
        assertThat(effectiveRate(null)).isEqualTo(20);
    }

    @Test
    void emptyTheEffectiveLimitIs20() {
        assertThat(effectiveRate("")).isEqualTo(20);
    }

    @Test
    void setItOverridesTheBaseline() {
        assertThat(effectiveRate("60")).isEqualTo(60);
    }

    /** The upload rate the gateway enforces with this value of GATEWAY_DOCS_UPLOADS_PER_MINUTE. */
    private static int effectiveRate(String value) {
        Map<String, Object> env = new HashMap<>();
        env.put("GATEWAY_DOCS_CONFIG", SHIPPED);
        if (value != null) {
            env.put("GATEWAY_DOCS_UPLOADS_PER_MINUTE", value);
        }
        MutablePropertySources sources = new MutablePropertySources();
        sources.addFirst(new SystemEnvironmentPropertySource("env", env));
        GatewayProperties.Docs docs = new Binder(ConfigurationPropertySources.from(sources))
                .bind("gateway.docs", GatewayProperties.Docs.class)
                .get();
        GatewayProperties properties = new GatewayProperties(null, null, null, null, null, docs, null, null);
        return new AttachmentsConfig().docsLimits(properties).uploadsPerMinute();
    }
}
