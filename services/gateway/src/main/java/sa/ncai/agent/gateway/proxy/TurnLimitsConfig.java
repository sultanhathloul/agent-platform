package sa.ncai.agent.gateway.proxy;

import java.nio.file.Path;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import sa.ncai.agent.gateway.GatewayProperties;

/** Loads config/turn.yaml once at startup; a bad file fails the gateway (as config/docs.yaml does). */
@Configuration(proxyBeanMethods = false)
class TurnLimitsConfig {

    @Bean
    TurnLimits turnLimits(GatewayProperties properties) {
        return TurnLimits.load(Path.of(properties.turn().config()));
    }
}
