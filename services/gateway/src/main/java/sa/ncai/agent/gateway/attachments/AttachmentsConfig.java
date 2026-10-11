package sa.ncai.agent.gateway.attachments;

import java.nio.file.Path;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import sa.ncai.agent.gateway.GatewayProperties;

/** Loads the upload limits once at startup; a bad docs config fails the gateway. */
@Configuration(proxyBeanMethods = false)
class AttachmentsConfig {

    @Bean
    DocsLimits docsLimits(GatewayProperties properties) {
        return DocsLimits.load(Path.of(properties.docs().config()));
    }
}
