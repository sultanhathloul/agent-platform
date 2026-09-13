package sa.ncai.agent.gateway.auth;

import java.nio.file.Path;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import sa.ncai.agent.gateway.GatewayProperties;

/** Loads the role tier caps once at startup; a bad models config fails the gateway (ADR-0046). */
@Configuration(proxyBeanMethods = false)
class TierCapsConfig {

    @Bean
    TierCaps tierCaps(GatewayProperties properties) {
        return TierCaps.load(Path.of(properties.models().config()));
    }
}
