package sa.ncai.agent.gateway.reports;

import java.nio.file.Path;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import sa.ncai.agent.gateway.GatewayProperties;

/** Loads the report kit once at startup; a bad report.yaml or a missing kit file fails the gateway. */
@Configuration(proxyBeanMethods = false)
class ReportsConfig {

    @Bean
    ReportKit reportKit(GatewayProperties properties) {
        return ReportKit.load(Path.of(properties.reports().config()), Path.of(properties.reports().kitDir()));
    }
}
