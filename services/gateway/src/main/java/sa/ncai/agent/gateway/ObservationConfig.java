package sa.ncai.agent.gateway;

import io.micrometer.observation.ObservationPredicate;
import java.util.List;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.server.observation.ServerRequestObservationContext;

/** Keeps probes and API docs out of traces so Langfuse shows one trace per user turn. */
@Configuration(proxyBeanMethods = false)
class ObservationConfig {

    private static final List<String> UNTRACED_PREFIXES = List.of("/actuator", "/v1/health", "/v3/api-docs");

    @Bean
    ObservationPredicate skipInfrastructureRequests() {
        return (name, context) -> !(context instanceof ServerRequestObservationContext server
                && isInfrastructure(server.getCarrier().getRequestURI()));
    }

    static boolean isInfrastructure(String uri) {
        return uri != null && UNTRACED_PREFIXES.stream().anyMatch(uri::startsWith);
    }
}
