package sa.ncai.agent.gateway;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.Map;
import org.junit.jupiter.api.Test;
import org.springframework.boot.SpringApplication;
import org.springframework.core.env.MapPropertySource;
import org.springframework.core.env.StandardEnvironment;

/**
 * The JDK HttpClient's keep-alive comes from {@code gateway.agent.keep-alive} (timeout budget PR, I8,
 * ADR-0260): shorter than the agent's 75 s, so the gateway never sends on a connection the agent has
 * closed. JDK 21.0.12, the image's, also defaults to 30 s (checked); the value is set, not inherited.
 */
class HttpClientKeepAliveTest extends GatewayTestSupport {

    @Test
    void theRunningGatewaySetsTheJdkKeepAliveFromItsConfiguration() {
        assertThat(System.getProperty(HttpClientKeepAlive.PROPERTY)).isEqualTo("30");
    }

    @Test
    void theValueFollowsTheProperty() {
        String before = System.getProperty(HttpClientKeepAlive.PROPERTY);
        try {
            StandardEnvironment environment = new StandardEnvironment();
            environment.getPropertySources()
                    .addFirst(new MapPropertySource("test", Map.of("gateway.agent.keep-alive", "20s")));
            new HttpClientKeepAlive().postProcessEnvironment(environment, new SpringApplication());
            assertThat(System.getProperty(HttpClientKeepAlive.PROPERTY)).isEqualTo("20");
        } finally {
            System.setProperty(HttpClientKeepAlive.PROPERTY, before);
        }
    }
}
