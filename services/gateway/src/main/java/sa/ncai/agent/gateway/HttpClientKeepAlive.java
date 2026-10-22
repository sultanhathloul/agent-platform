package sa.ncai.agent.gateway;

import java.time.Duration;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.context.properties.bind.Binder;
import org.springframework.boot.env.EnvironmentPostProcessor;
import org.springframework.core.env.ConfigurableEnvironment;

/**
 * Sets the JDK HttpClient's idle keep-alive from {@code gateway.agent.keep-alive} (timeout budget PR,
 * I8, ADR-0260) before any client exists: the JDK reads {@code jdk.httpclient.keepalive.timeout}
 * once, when its first HttpClient is created, and every client in the process shares it. A
 * connection the gateway keeps idle must be dropped before the agent's server drops it (uvicorn
 * {@code --timeout-keep-alive 75}), or the gateway may send a request on a connection the agent
 * has just closed.
 */
public class HttpClientKeepAlive implements EnvironmentPostProcessor {

    static final String PROPERTY = "jdk.httpclient.keepalive.timeout";

    @Override
    public void postProcessEnvironment(ConfigurableEnvironment environment, SpringApplication application) {
        Binder.get(environment)
                .bind("gateway.agent.keep-alive", Duration.class)
                .ifBound(idle -> System.setProperty(PROPERTY, Long.toString(idle.toSeconds())));
    }
}
